
import asyncio
import websockets
import json
import logging
import os
import signal
import psutil

# Configure logging
LOG_FILE = r"C:\tmp\native_server.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [NativeServer] - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PORT = 9223
HOST = "127.0.0.1"

# Connection Registry
# We expect one 'browser' connection and multiple 'agent' connections
connections = {
    "browser": None,
    "agents": set()
}

async def kill_zombies(port):
    """Find and kill any process listening on the target port"""
    logger.info(f"Checking for zombie processes on port {port}...")
    current_pid = os.getpid()
    
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            for conn in proc.net_connections(kind='inet'):
                if conn.laddr.port == port:
                    if proc.pid != current_pid:
                        logger.warning(f"Killing zombie process: {proc.name()} (PID: {proc.pid})")
                        proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

async def handler(websocket):
    """Handle incoming WebSocket connections"""
    client_type = "unknown"
    
    try:
        # Initial handshake: Client must send its type
        handshake = await websocket.recv()
        data = json.loads(handshake)
        client_type = data.get("type")
        
        if client_type == "browser":
            logger.info("Browser Extension Connected!")
            connections["browser"] = websocket
            try:
                async for message in websocket:
                    msg_data = json.loads(message)
                    
                    # Handle Heartbeat Pong
                    if msg_data.get("pong"):
                        # logger.debug("Received pong from browser")
                        continue
                        
                    # Forward browser responses to all agents
                    # In a real system, we'd use an ID to route to the specific requester
                    # For now, broadcast results
                    if connections["agents"]:
                        disconnected = set()
                        for agent in connections["agents"]:
                            try:
                                await agent.send(message)
                            except websockets.exceptions.ConnectionClosed:
                                disconnected.add(agent)
                        connections["agents"] -= disconnected
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Browser Disconnected!")
                connections["browser"] = None
                
        elif client_type == "agent":
            logger.info("Nexus Agent Connected!")
            connections["agents"].add(websocket)
            try:
                async for message in websocket:
                    # Forward agent commands to browser
                    if connections["browser"]:
                        try:
                            await connections["browser"].send(message)
                            logger.info(f"Forwarded command: {message[:100]}...")
                        except websockets.exceptions.ConnectionClosed:
                            logger.error("Cannot forward: Browser disconnected")
                            connections["browser"] = None
                            await websocket.send(json.dumps({"error": "Browser disconnected"}))
                    else:
                        logger.warning("No browser connected to receive command")
                        await websocket.send(json.dumps({"error": "Browser not connected"}))
                        
            except websockets.exceptions.ConnectionClosed:
                logger.info("Agent Disconnected")
                connections["agents"].remove(websocket)
                
        else:
            logger.error(f"Unknown client type: {client_type}")
            await websocket.close()

    except Exception as e:
        logger.error(f"Connection error: {e}")
    finally:
        # Cleanup
        if client_type == "browser" and connections["browser"] == websocket:
            connections["browser"] = None
        elif client_type == "agent" and websocket in connections["agents"]:
            connections["agents"].remove(websocket)

async def main():
    # Kill zombies first
    await kill_zombies(PORT)
    
    logger.info(f"Starting WebSocket Server on {HOST}:{PORT}")
    async with websockets.serve(handler, HOST, PORT):
        # Keep server running until signal
        stop = asyncio.Future()
        await stop

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopping...")
