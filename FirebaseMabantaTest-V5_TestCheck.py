import firebase_admin
from firebase_admin import credentials, db
import json
import time
import threading
import serial
import modbus_tk.defines as cst
from modbus_tk import modbus_rtu
from gpiozero import LED
from datetime import datetime

switch = LED(17)

class FirebaseManager:
    def __init__(self, cert_file, db_url):
        self.cred = credentials.Certificate(cert_file)
        self.db_url = db_url
        self.room_ref = None
        self.initialize_firebase()

    def initialize_firebase(self):
        firebase_admin.initialize_app(self.cred, {'databaseURL': self.db_url})
        self.RoomRef = db.reference('/Rooms/Room-1')

    def getFirebase(self):
        return self.RoomRef.get()

class LocalDataManager:
    def __init__(self, json_file):
        self.json_file = json_file

    def readLocal(self):
        with open(self.json_file, 'r') as f:
            return json.load(f)

    def updateLocal(self, data):
        with open(self.json_file, 'w') as f:
            json.dump(data, f, indent=4)

class Pzem004T:
    def __init__(self, port):
        self.serial = serial.Serial(
            port='/dev/ttyUSB0',
            baudrate=9600,
            bytesize=8,
            parity='N',
            stopbits=1,
            xonxoff=0
        )
        
        self.master = modbus_rtu.RtuMaster(self.serial)
        self.master.set_timeout(2.0)
        self.master.set_verbose(True)

    def PzemSensorDataRead(self):
        try:
            data = self.master.execute(1, cst.READ_INPUT_REGISTERS, 0, 10)
            voltage = data[0] / 10.0  # [V]
            current = (data[1] + (data[2] << 16)) / 1000.0  # [A]
            power = (data[3] + (data[4] << 16)) / 10.0  # [W]
            return power
        except ModbusInvalidResponseError:
            pass
        

class ElectricityController:
    def __init__(self):
        self.Firebase = FirebaseManager('econtrollectricity-firebase-adminsdk-r45sa-d9f7151c8b.json',
                                                'https://econtrollectricity-default-rtdb.asia-southeast1.firebasedatabase.app/')
        self.localManager = LocalDataManager('TestJson.json')
        self.lastFirebaseData = None
        self.lastLocalData = None
        self.pzem_sensor = Pzem004T('/dev/ttyUSB0')

    def handle_firebase_updates(self):
        while True:
            try:
                FirebaseData = self.Firebase.getFirebase()

                if self.lastFirebaseData is None:
                    self.lastFirebaseData = FirebaseData

                if FirebaseData != self.lastFirebaseData:
                    # Firebase data for Room-1 was updated, so update local JSON
                    self.lastLocalData["Rooms"]["Room-1"] = FirebaseData
                    self.localManager.updateLocal(self.lastLocalData)
                    print('Change detected in Room-1 in the Firebase database')
                    self.lastFirebaseData = FirebaseData
                time.sleep(1)

            except firebase_admin.exceptions.UnavailableError:
                print('Firebase is currently unavailable. Storing changes locally.')

            except Exception as e:
                print('No Internet Detected, Connect to Internet to send data to synchronize Data from Local DB to Firebase')
            time.sleep(3)
        
    def handle_local_updates(self):
        while True:
            try:
                # Read local data once per iteration
                localData = self.localManager.readLocal()

                if self.lastLocalData is None:
                    self.lastLocalData = localData

                if localData != self.lastLocalData:
                    # Extract the fields you want to update
                    power_consumption = localData["Rooms"]["Room-1"]["PowerConsumption"]
                    current_credit = localData["Rooms"]["Room-1"]["CurrentCredit"]
                    
                    # Create a dictionary with only the fields you want to update
                    update_data = {
                        "PowerConsumption": power_consumption,
                        "CurrentCredit": current_credit
                    }

                    # Update the Firebase fields with the extracted values
                    self.Firebase.RoomRef.update(update_data)

                    print('Change detected in Room-1 in the local JSON file')
                    self.lastLocalData = localData
                    time.sleep(1.50)

                # Update PowerConsumption with the current time and power value
                power = self.pzem_sensor.PzemSensorDataRead()
                power_in_kWh = (power / 1000) / 3600  # Convert power to kilowatt-hours
                current_datetime = datetime.now().strftime('%m-%d-%Y %H:%M:%S')
                power_consumption = round(power_in_kWh, 7)
                if power > 0:  # Only update PowerConsumption if power is greater than 0
                    localData["Rooms"]["Room-1"]["PowerConsumption"][current_datetime] = power_consumption
                    # Deduct from CurrentCredit
                    electricity_price = localData["Rooms"]["Room-1"]["ElectricityPrice"]
                    current_credit = localData["Rooms"]["Room-1"]["CurrentCredit"]
                    deduction = power_consumption * electricity_price
                    updated_credit = current_credit - deduction

                    # Make sure the updated_credit is never negative
                    updated_credit = max(updated_credit, 0)

                    localData["Rooms"]["Room-1"]["CurrentCredit"] = updated_credit

                self.localManager.updateLocal(localData)

            except Exception as e:
                print('Error: ', e)
                time.sleep(1)

            finally:
                localData = self.localManager.readLocal()
                if localData["Rooms"]["Room-1"]["CurrentCredit"] > 0:
                    switch.on()
                else:
                    switch.off()
                self.localManager.updateLocal(localData)
                
    def run(self):
        firebase_thread = threading.Thread(target=self.handle_firebase_updates)
        firebase_thread.daemon = True
        firebase_thread.start()
        local_thread = threading.Thread(target=self.handle_local_updates)
        local_thread.daemon = True
        local_thread.start()

if __name__ == "__main__":
    electricity_controller = ElectricityController()
    electricity_controller.run()
