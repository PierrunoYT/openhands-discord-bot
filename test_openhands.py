"""
Test script to verify OpenHands API connection
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
from openhands_client import OpenHandsClient

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

async def test_connection():
    api_key = os.getenv("OPENHANDS_API_KEY")
    base_url = os.getenv("OPENHANDS_BASE_URL", "https://app.all-hands.dev/api")
    repo = os.getenv("GITHUB_REPO")
    
    print(f"Testing OpenHands API connection...")
    print(f"API Key: {api_key[:10]}... (hidden)")
    print(f"Base URL: {base_url}")
    print(f"Repository: {repo}")
    print("-" * 50)
    
    if not api_key or api_key.startswith("ohc_xxx"):
        print("[ERROR] OPENHANDS_API_KEY is not set or is still the placeholder value")
        return
    
    if not repo or repo == "deinuser/dein-repo":
        print("[ERROR] GITHUB_REPO is not set or is still the placeholder value")
        return
    
    client = OpenHandsClient(api_key=api_key, base_url=base_url)
    
    try:
        print("\n[TEST] Creating a test conversation...")
        result = await client.create_conversation(
            task="Test connection - please respond with 'Hello'",
            repository=repo
        )
        
        print("[SUCCESS] API connection working!")
        print(f"Response: {result}")
        
        conv_id = result.get("conversation_id") or result.get("id")
        if conv_id:
            print(f"\nConversation ID: {conv_id}")
            print(f"View at: https://app.all-hands.dev/conversations/{conv_id}")
            
            print("\n[TEST] Checking conversation status...")
            status = await client.get_conversation_status(conv_id)
            print(f"[SUCCESS] Status: {status.get('status', 'unknown')}")
        
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}")
        print(f"Message: {str(e)}")
        
        if hasattr(e, 'status'):
            print(f"HTTP Status: {e.status}")
        if hasattr(e, 'message'):
            print(f"Response: {e.message[:500]}")
    
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(test_connection())
