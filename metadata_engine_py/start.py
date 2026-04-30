import os
import time
import socket
import uvicorn

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def kill_port_owner(port: int):
    print(f"Purging existing bindings on port {port}...")
    try:
        os.system(f"kill -9 $(lsof -t -i:{port}) 2>/dev/null")
    except Exception as e:
        print("Bypass:", e)

if __name__ == "__main__":
    PORT = 7000
    
    if is_port_in_use(PORT):
        kill_port_owner(PORT)
        time.sleep(1)
        
    print("Initiating Python Metadata Engine NLP Backend...")
    
    # Natively mount the ASGI worker within the Python interpreter boundary 
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
