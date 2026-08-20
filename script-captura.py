import psutil
import csv
import time
from datetime import datetime

machine_name = input("Digite o nome da sua máquina: ")

lastTime = 0

ram = 0
disk = 0
cpu_geral_percent = psutil.cpu_percent(None)

with open(f"./csv/{machine_name}.csv", "w") as csvfile:
    csv.writer(csvfile, delimiter=';').writerow(['machine_name','timestamp', 'cpu_usage_percent', 'ram_usage_percent', 'disk_usage_percent'])

while True:
    if time.time() - lastTime >= 5:
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu_geral_percent = psutil.cpu_percent(None)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(f"./csv/{machine_name}.csv", "a") as csvfile:
            csv.writer(csvfile, delimiter=';').writerow([machine_name,now, cpu_geral_percent, ram.percent, disk.percent])

        print("Salvo:", [machine_name,now, cpu_geral_percent, ram.percent, disk.percent])
        lastTime = time.time()


