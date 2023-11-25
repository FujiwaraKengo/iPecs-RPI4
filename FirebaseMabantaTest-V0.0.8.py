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
import ntplib

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
            print("Initialize Firebase Error: ", e)

    def getFirebase(self):
        try:
            return self.RoomRef.get()
        except Exception as e:
            print("Get Firebase Error: ", e)
            pass

    def updateFirebase(self, data):
        try:
            return self.RoomRef.update(data)
        except Exception as e:
            print("Update Firebase Error: ", e)
            pass

class LocalDataManager:
    def __init__(self, json_file):
        self.json_file = json_file

    def readLocal(self):
        try:
            with open(self.json_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print("Read Local Error: ", e)

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
            self.serial.close()  # Close the serial connection
            self.serial.open()  # Reopen the serial connection
            data = self.master.execute(1, cst.READ_INPUT_REGISTERS, 0, 10)
            voltage = data[0] / 10.0  # [V]
            current = (data[1] + (data[2] << 16)) / 1000.0  # [A]
            power = (data[3] + (data[4] << 16)) / 10.0  # [W]
            print(power)
            return power
        except ModbusInvalidResponseError as e:
            print("Pzem Reader Error: ", e)
            return None

class ElectricityController:
    def set_time():
        ntp_client = ntplib.NTPClient()
        response = ntp_client.request('pool.ntp.org')
        current_time = datetime.fromtimestamp(response.tx_time)
        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
        command = f'sudo date -s "{formatted_time}"'
        os.system(command)

    def __init__(self):
        try:
            self.Firebase = FirebaseManager('econtrollectricity-firebase-adminsdk-r45sa-d9f7151c8b.json',
                                           'https://econtrollectricity-default-rtdb.asia-southeast1.firebasedatabase.app/')
        except Exception as e:
            print("Main class error: ", e)
        self.localManager = LocalDataManager('TestJson.json')
        self.lastFirebaseData = None
        self.lastLocalData = None
        self.pzem_sensor = Pzem004T('/dev/ttyUSB0')

    def handle_updates(self):
        while True:
            FirebaseData = self.Firebase.getFirebase()
            
            # Read local data once per iteration
            localData = self.localManager.readLocal()

            # Update firebase based on local data once per iteration
            FirebaseUpdate = self.Firebase.updateFirebase()


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
                
                # try:
                #     #Update the Firebase fields with the extracted values
                #     self.Firebase.RoomRef.update(update_data)
                # except Exception as e:
                #     print("Referrence Firebase Error: ", e)

                    #Update the Firebase fields with the extracted values
                FirebaseUpdate(update_data)

                print('Change detected in Room-1 in the local JSON file')
                self.lastLocalData = localData
            self.localManager.updateLocal(localData)
            time.sleep(1)

                   
#----------------------------------------------------------------------------------------#
            if self.lastFirebaseData is None:
                self.lastFirebaseData = FirebaseData

            if FirebaseData is not None:
                if FirebaseData != self.lastFirebaseData:
                    # Firebase data for Room-1 was updated, so update local JSON
                    required_fields = {
                        "CreditCriticalLevel": self.lastFirebaseData["CreditCriticalLevel"],
                        "CurrentCredit": self.lastFirebaseData["CurrentCredit"],
                        "ElectricityPrice": self.lastFirebaseData["ElectricityPrice"],
                    }
                    self.lastLocalData["Rooms"]["Room-1"].update(required_fields)
                    self.localManager.updateLocal(self.lastLocalData)
                    print('Change detected in Room-1 in the Firebase database')
                    self.lastFirebaseData = FirebaseData
            time.sleep(1) 


            if localData["Rooms"]["Room-1"]["CurrentCredit"] > 0:
                switch.on()
            else:
                switch.off()
            
                
    def PzemToLocalData(self):
        while True:
            # Read local data once per iteration
            localData = self.localManager.readLocal()

            # Check if localData is None, and revert to the last saved local data
            if localData is None:
                localData = self.lastLocalData
                print("Reverted to the last saved local data")

            # Update PowerConsumption with the current time and power value
            power = self.pzem_sensor.PzemSensorDataRead()
            if power is not None:
                power_in_kWh = ((power / 1000) / 3600)  # Convert power to kilowatt-hours
                current_datetime = datetime.now().strftime('%m-%d-%Y %H:%M:%S')
                power_consumption = round(power_in_kWh, 7)
                if power_consumption > 0:  # Only update PowerConsumption if power is greater than 0
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
            time.sleep(1)
                
    def run(self):
        self.set_time()
        pzem_thread = threading.Thread(target=self.PzemToLocalData)
        pzem_thread.daemon = True
        pzem_thread.start()
        db_thread = threading.Thread(target=self.handle_updates)
        db_thread.daemon = True
        db_thread.start()


if __name__ == "__main__":
    electricity_controller = ElectricityController()
    electricity_controller.run()