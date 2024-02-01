import firebase_admin
from firebase_admin import credentials, db
import json
import time
import threading
import serial
import modbus_tk
import modbus_tk.defines as cst
from modbus_tk import modbus_rtu
from gpiozero import LED
from datetime import datetime


switch = LED(17)

class FirebaseManager:
    def __init__(self, cert_file, db_url):
        self.cred = credentials.Certificate(cert_file)
        self.db_url = db_url
        self.initialize_firebase()

    def initialize_firebase(self):
        try:
            firebase_admin.initialize_app(self.cred, {'databaseURL': self.db_url, 'databasePersistence': True})
            self.RoomRef = db.reference('/Rooms/Room-1')
        except firebase_admin.exceptions.UnavailableError:
            print('Failed to Initialize Waiting for Internet')
            time.sleep(1)

    def get_firebase_data(self):
        try:
            return self.RoomRef.get()
        except firebase_admin.exceptions.UnavailableError:
            print('No Internet Detected, Storing Data Locally')
            return None
            time.sleep(1)
        except Exception as e:
            print(f"Get Firebase Error: {e}")
            return None
            time.sleep(1)

    def update_firebase_data(self, data):
        try:
            return self.RoomRef.update(data)
            print("Sending data to Firebase")
            time.sleep(1)
        except Exception as e:
            print("Update Firebase Error: ", e)
            time.sleep(1)

class LocalDataManager:
    def __init__(self, json_file, backup_file):
        self.json_file = json_file
        self.backup_file = backup_file

    def read_local_data(self):
        try:
            with open(self.json_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print("Read Local Error: ", e)

    def update_local_data(self, data):
        try:
            print("Updating Local")
            self.create_backup()
            with open(self.json_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print("Update Local Error: ", e)
            
    def update_room_data(self, local_data, key, value):
        try:
            room_data = local_data.get("Rooms", {}).get("Room-1", {})
            room_data[key] = value
            local_data["Rooms"]["Room-1"] = room_data
        except Exception as e:
            print("Update Room Data Error: ", e)

    def get_room_data(self, local_data, key):
        try:
            return local_data.get("Rooms", {}).get("Room-1", {}).get(key)
        except Exception as e:
            print("Get Room Data Error: ", e)
            return 0
    def update_power_consumption(self, local_data, current_datetime, power_consumption):
        try:
            room_data = local_data.get("Rooms", {}).get("Room-1", {})
            power_data = room_data.get("PowerConsumption", {})
            power_data[current_datetime] = power_consumption
            room_data["PowerConsumption"] = power_data
            local_data["Rooms"]["Room-1"] = room_data
        except Exception as e:
            print("Update Power Consumption Error: ", e)
            
    def create_backup(self):
        try:
            with open(self.json_file, 'r') as f:
                backup_data = json.load(f)
            with open(self.backup_file, 'w') as backup_file:
                json.dump(backup_data, backup_file, indent=4)
        except Exception as e:
            print("Backup Error: ", e)
                
    def restore_from_backup(self):
        try:
            with open(self.backup_file, 'r') as backup_file:
                backup_data = json.load(backup_file)
            with open(self.json_file, 'w') as f:
                json.dump(backup_data, f, indent=4)
            print("Restoring Data from Backup.")
        except Exception as e:
            print("Restoring Failed: ", e)

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

    def pzem_sensor_data_read(self):
        try:
            self.serial.close()  # Close the serial connection
            self.serial.open()  # Reopen the serial connection
            data = self.master.execute(1, cst.READ_INPUT_REGISTERS, 0, 10)
            voltage = data[0] / 10.0  # [V]
            current = (data[1] + (data[2] << 16)) / 1000.0  # [A]
            power = (data[3] + (data[4] << 16)) / 10.0  # [W]
            print(power)
            return power
        except modbus_tk.exceptions.ModbusInvalidResponseError as e:
            print("Pzem Reader Error: ", e)
            return None

class ElectricityController:
    def __init__(self):
        self.firebase_manager = FirebaseManager('/home/capstone/Downloads/econtrollectricity-firebase-adminsdk-r45sa-d9f7151c8b.json', 'https://econtrollectricity-default-rtdb.asia-southeast1.firebasedatabase.app/')
        self.local_manager = LocalDataManager('/home/capstone/Downloads/TestJson.json', '/home/capstone/Downloads/TestJson_backup.json')
        self.pzem_sensor = Pzem004T('/dev/ttyUSB0')
        self.connection = False
        self.snapshot_current_credit = 0
        
        self.local_data_lock = threading.Lock()

    def handle_updates(self):
        while True:
            time.sleep(.25)
            print("InternetCheck")
            firebase_data = self.firebase_manager.get_firebase_data()
            
            with self.local_data_lock:
                if firebase_data is not None:
                    #Fetch specific fields from Firebase
                    current_credit_firebase = firebase_data['CurrentCredit']
                    electricity_price = firebase_data['ElectricityPrice']
                    credit_critical_level = firebase_data['CreditCriticalLevel']
                    #Reads the Local Json
                    local_data = self.local_manager.read_local_data()
                    local_current_credit = self.local_manager.get_room_data(local_data, "CurrentCredit")
                    #print("__________________________________________________________________")
                    #print(f"Starting Local Data of Loop: {local_data}")
                    #print(f"Firebase Current Credit is: {current_credit_firebase}")
                    #print("Local Current Credit is: ", local_current_credit)
                    #print("Snapshot Data Is: ", self.snapshot_current_credit)

                    #Checks if Firebase and Local is the Same
                    if current_credit_firebase != local_current_credit:
                        #print('Checking for Changes...')
                    #Checks is the Snapshot is Higher than the Local Data
                        if self.snapshot_current_credit >= local_current_credit:
                            # Calculate the difference between snapshot and local JSON CurrentCredit
                            #print("Calculating Changes on Local: ", self.snapshot_current_credit, " minus ", local_current_credit)

                            difference = self.snapshot_current_credit - local_current_credit
                            #print(f"Answer Is: {difference}")

                            # Adjust Firebase CurrentCredit with the difference
                            #print(f"Adjusting Credit: {current_credit_firebase} minus {difference}")
                            current_credit_firebase -= difference
                            #print(f"Current Credit Is Now: {current_credit_firebase}")

                    # Update snapshot variable with the adjusted CurrentCredit value
                    self.snapshot_current_credit = local_current_credit
                    #print("Snapshot Data now Is: ", self.snapshot_current_credit)

                    # Update local JSON with the adjusted Firebase CurrentCredit
                    self.local_manager.update_room_data(local_data, "CurrentCredit", current_credit_firebase)
                    self.local_manager.update_room_data(local_data, "CreditCriticalLevel", credit_critical_level)
                    self.local_manager.update_room_data(local_data, "ElectricityPrice", electricity_price)
                    self.local_manager.update_local_data(local_data)
                    self.firebase_manager.update_firebase_data({"CurrentCredit": current_credit_firebase})
                    self.firebase_manager.update_firebase_data({"PowerConsumption":  local_data["Rooms"]["Room-1"]["PowerConsumption"]})

    def pzem_to_local_data(self):
        while True:
            local_data = self.local_manager.read_local_data()
            if local_data is not None:
                if self.local_manager.get_room_data(local_data, "CurrentCredit") > 0:
                    switch.on()
                    power = self.pzem_sensor.pzem_sensor_data_read()

                    if power is not None:
                        power_in_kWh = ((power / 1000) / 3600)
                        current_datetime = datetime.now().strftime('%m-%d-%Y %H:%M:%S')
                        power_consumption = round(power_in_kWh, 7)

                        if power_consumption > 0:
                            # Update local JSON directly
                            self.local_manager.update_power_consumption(local_data, current_datetime, power_consumption)
                            electricity_price = self.local_manager.get_room_data(local_data, "ElectricityPrice")
                            deduction = power_consumption * electricity_price
                            updated_credit = max(self.local_manager.get_room_data(local_data, "CurrentCredit") - deduction, 0)
                            self.local_manager.update_room_data(local_data, "CurrentCredit", updated_credit)

                            self.local_manager.update_local_data(local_data)       
                else:
                    switch.off()
            time.sleep(1)
            

    def run(self):
        pzem_thread = threading.Thread(target=self.pzem_to_local_data)
        pzem_thread.start()
        db_thread = threading.Thread(target=self.handle_updates)
        db_thread.start()

if __name__ == "__main__":
    electricity_controller = ElectricityController()
    electricity_controller.run()