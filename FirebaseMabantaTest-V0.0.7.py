import firebase_admin
from firebase_admin import credentials, db
import json
import time
import threading
import serial
import modbus_tk.defines as cst
from modbus_tk import modbus_rtu
from modbus_tk.exceptions import ModbusInvalidResponseError
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
        try:
            firebase_admin.initialize_app(self.cred, {'databaseURL': self.db_url})
            self.RoomRef = db.reference('/Rooms/Room-1')
        except Exception as e:
            print("Initilize Firebase Error: ", e)

    def getFirebase(self):
        try:
            return self.RoomRef.get()
        except Exception as e:
            print("Get Firebase Error: ", e)
            return None

class LocalDataManager:
    def __init__(self, json_file):
        self.json_file = json_file

    def readLocal(self):
        try:
            with open(self.json_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print("Read Local Error: ", e)
            return None

    def updateLocal(self, data):
        try:
            with open(self.json_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print("Update Local Error: ", e)

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
        except ModbusInvalidResponseError as e:
            print("Pzem Reader Error: ", e)
            return None
        

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
            FirebaseData = self.Firebase.getFirebase()
            LocalData = self.localManager.readLocal()

            if self.lastFirebaseData is None:
                self.lastFirebaseData = FirebaseData

            if FirebaseData != self.lastFirebaseData:
                # Update only the required fields in the local JSON
                self.lastLocalData["Rooms"]["Room-1"]["CurrentCredit"] = FirebaseData.get("CurrentCredit")
                self.lastLocalData["Rooms"]["Room-1"]["CreditCriticalLevel"] = FirebaseData.get("CreditCriticalLevel")
                self.lastLocalData["Rooms"]["Room-1"]["ElectricityPrice"] = FirebaseData.get("ElectricityPrice")
                self.localManager.updateLocal(self.lastLocalData)
                print('Change detected in Room-1 in the Firebase database')
                self.lastFirebaseData = FirebaseData
                time.sleep(1)

        
    def handle_local_updates(self):
        while True:
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
                time.sleep(1)


            localData = self.localManager.readLocal()
            if localData["Rooms"]["Room-1"]["CurrentCredit"] > 0:
                switch.on()
            else:
                switch.off()
            self.localManager.updateLocal(localData)
                
    def PzemToLocalData(self):
        pzemRead = self.localManager.readLocal()
        # Update PowerConsumption with the current time and power value
        power = self.pzem_sensor.PzemSensorDataRead()
        if power is not None:
            power_in_kWh = (power / 1000) / 3600  # Convert power to kilowatt-hours
            current_datetime = datetime.now().strftime('%m-%d-%Y %H:%M:%S')
            power_consumption = round(power_in_kWh, 7)
            if power > 0:  # Only update PowerConsumption if power is greater than 0
                pzemRead["Rooms"]["Room-1"]["PowerConsumption"][current_datetime] = power_consumption
                # Deduct from CurrentCredit
                electricity_price = pzemRead["Rooms"]["Room-1"]["ElectricityPrice"]
                current_credit = pzemRead["Rooms"]["Room-1"]["CurrentCredit"]
                deduction = power_consumption * electricity_price
                updated_credit = current_credit - deduction
                # Make sure the updated_credit is never negative
                updated_credit = max(updated_credit, 0)

                pzemRead["Rooms"]["Room-1"]["CurrentCredit"] = updated_credit

                # Update only the "PowerConsumption" field in the local JSON
                self.localManager.updateLocal(pzemRead)
                
    def run(self):
        firebase_thread = threading.Thread(target=self.handle_firebase_updates)
        firebase_thread.daemon = True
        firebase_thread.start()
        pzem_thread = threading.Thread(target=self.PzemToLocalData)
        pzem_thread.daemon = True
        pzem_thread.start()
        local_thread = threading.Thread(target=self.handle_local_updates)
        local_thread.daemon = True
        local_thread.start()

if __name__ == "__main__":
    electricity_controller = ElectricityController()
    electricity_controller.run()
