import asyncio
import nats
import logging
from nats.errors import ConnectionClosedError, TimeoutError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - VERIFIER - %(levelname)s - %(message)s')
logger = logging.getLogger("NATSVerifier")

async def main():
    try:
        # Connect to NATS
        nc = await nats.connect("nats://localhost:4222")
        js = nc.jetstream()
        logger.info("Connected to NATS")

        # Subscribe to semantic stream
        # Create consumer
        psub = await js.pull_subscribe("browser.semantic", durable="verifier")
        logger.info("Subscribed to browser.semantic. Waiting for events...")

        # Fetch messages
        try:
            msgs = await psub.fetch(5, timeout=10) # Wait 10s
            for msg in msgs:
                logger.info(f"Received Message: {len(msg.data)} bytes")
                # We expect protobuf data.
                # Just print first few bytes
                logger.info(f"Data Head: {msg.data[:20]}")
                await msg.ack()
                
            logger.info("SUCCESS: Received semantic events from NATS!")
            
        except TimeoutError:
            logger.error("FAILURE: Timed out waiting for events. Is Chrome running and on a page?")
        except Exception as e:
            logger.error(f"FAILURE: {e}")

        await nc.close()

    except Exception as e:
        logger.error(f"Connection failed: {e}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
