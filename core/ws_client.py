import asyncio
import websockets

async def run():
    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri) as ws:
        print("WS connected")
        try:
            while True:
                msg = await ws.recv()
                print("WS RECV:", msg)
        except Exception as exc:
            print('WS client error', exc)

if __name__ == '__main__':
    asyncio.run(run())
