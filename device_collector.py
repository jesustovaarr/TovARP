import json
import os
import yaml
from datetime import datetime
from netmiko import ConnectHandler

# cargar credenciales del dispositivo
def cargar_configuracion(ruta="config/device.yaml"):
    with open(ruta, "r") as f:
        config = yaml.safe_load(f)
    return config["device"]

# conexion ssh al dispositivo
def conectar(device_config):
    print(f"Conectando a {device_config['host']}")
    conexion = ConnectHandler(**device_config)
    print("Conexion Correcta")
    return conexion

# version, modelo y uptime
def obtener_version(conexion):
    salida = conexion.send_command("show version")
    version_ios = "N/A"
    modelo = "N/A"
    uptime = "N/A"

    for linea in salida.splitlines():
        partes = linea.split()

        if "Version" in partes:
            idx = partes.index("Version")
            version_ios = partes[idx + 1]

        if partes and partes[0] == "cisco":
            modelo = partes[1]

        if "uptime" in partes and "is" in partes:
            idx = partes.index("is")
            uptime = " ".join(partes[idx + 1:])

    return {
        "version_ios": version_ios,
        "modelo": modelo,
        "uptime": uptime
    }

# detalles de interfaces
def obtener_interfaces(conexion):
    salida = conexion.send_command("show ip interface brief")
    interfaces = []
    for linea in salida.splitlines()[1:]:
        partes = linea.split()
        if len(partes) >= 6:
            interfaces.append({
                "interfaz": partes[0],
                "ip": partes[1],
                "estado": partes[4],
                "protocolo": partes[5]
            })
    return interfaces

# tabla de ruteo
def obtener_rutas(conexion):
    salida = conexion.send_command("show ip route")
    rutas = []
    tipo_map = {
        "C": "Conectada", "S": "Estática", "O": "OSPF",
        "R": "RIP", "B": "BGP", "D": "EIGRP",
        "E": "EIGRP externo", "I": "IGRP"
    }
    for linea in salida.splitlines():
        partes = linea.split()
        if not partes or partes[0] not in tipo_map:
            continue
        red = next((p for p in partes if "." in p and p[0].isdigit()), None)
        if red is None:
            continue
        idx = partes.index(red)
        detalle = " ".join(partes[idx + 1:])
        rutas.append({
            "tipo":    tipo_map[partes[0]],
            "red":     red,
            "detalle": detalle
        })
    return rutas

# vecinos cdp
def obtener_vecinos_cdp(conexion):
    salida = conexion.send_command("show cdp neighbors")
    vecinos = []
    for linea in salida.splitlines()[3:]:
        partes = linea.split()
        if len(partes) >= 5:
            vecinos.append({
                "dispositivo": partes[0],
                "interfaz_local": partes[1] + " " + partes[2],
                "capacidad": partes[3],
                "plataforma": partes[4] if len(partes) > 4 else "N/A"
            })
    return vecinos

# reporte JSON
def generar_reporte(datos, directorio="reports"):
    print("Generando reporte")
    os.makedirs(directorio, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f"{directorio}/reporte_{timestamp}.json"
    datos["archivo_reporte"] = nombre_archivo
    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=4, ensure_ascii=False)
    return nombre_archivo

def main():
    device_config = cargar_configuracion()
    conexion = conectar(device_config)

    # extraer datos
    version_info = obtener_version(conexion)
    interfaces = obtener_interfaces(conexion)
    rutas = obtener_rutas(conexion)
    vecinos_cdp = obtener_vecinos_cdp(conexion)

    conexion.disconnect()
    print("Conexion finalizada")

    # estructura de datos
    datos = {
        "host": device_config["host"],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "informacion_dispositivo": version_info,
        "interfaces": interfaces,
        "rutas": rutas,
        "vecinos_cdp": vecinos_cdp,
    }

    generar_reporte(datos)

if __name__ == "__main__":
    main()