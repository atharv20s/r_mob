import urllib.request
import json
import time

url_sync = "http://localhost:8000/api/v1/chat"
url_stream = "http://localhost:8000/api/v1/chat/stream"
url_openai = "http://localhost:8000/v1/chat/completions"
headers = {
    "Authorization": "Bearer sk_user_test_key_12345",
    "Content-Type": "application/json"
}

def test_sync():
    print("=== Testing Sync Chat completion (/api/v1/chat) ===")
    data = {
        "prompt": "Say: Hello world from Route Mobile API!",
        "model": "qwen3",
        "temperature": 0.2
    }
    req = urllib.request.Request(
        url_sync,
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST"
    )
    try:
        start = time.time()
        r = urllib.request.urlopen(req)
        resp = json.loads(r.read().decode())
        print(f"Status Code: {r.status}")
        print(f"Latency: {round((time.time() - start) * 1000)}ms")
        print(f"Response: {resp.get('response')}")
        print(f"Provider: {resp.get('provider')}, Model: {resp.get('model')}")
        print(f"Usage: {resp.get('usage')}")
    except urllib.error.HTTPError as e:
        print(f"Error in Sync Chat: {e.code} {e.reason} - {e.read().decode()}")
    except Exception as e:
        print(f"Error in Sync Chat: {e}")

def test_stream():
    print("\n=== Testing Streaming Chat completion (/api/v1/chat/stream) ===")
    data = {
        "prompt": "Write a 3-word sentence.",
        "model": "gemma",
        "temperature": 0.2
    }
    req = urllib.request.Request(
        url_stream,
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST"
    )
    try:
        start = time.time()
        r = urllib.request.urlopen(req)
        print(f"Status Code: {r.status}")
        print("Stream content: ", end="", flush=True)
        for line in r:
            line_decoded = line.decode().strip()
            if line_decoded.startswith("data: "):
                try:
                    payload = json.loads(line_decoded[6:])
                    token = payload.get("token", "")
                    if token:
                        print(token, end="", flush=True)
                    if payload.get("done"):
                        print(f"\n[DONE] Usage: {payload.get('usage')}")
                except Exception:
                    pass
        print(f"Total Stream Time: {round((time.time() - start) * 1000)}ms")
    except urllib.error.HTTPError as e:
        print(f"Error in Stream Chat: {e.code} {e.reason} - {e.read().decode()}")
    except Exception as e:
        print(f"Error in Stream Chat: {e}")

def test_openai():
    print("\n=== Testing OpenAI-Compatible completion (/v1/chat/completions) ===")
    data = {
        "model": "llama3",
        "messages": [{"role": "user", "content": "Say: Hello from OpenAI SDK!"}],
        "temperature": 0.2,
        "stream": False
    }
    req = urllib.request.Request(
        url_openai,
        data=json.dumps(data).encode(),
        headers=headers,
        method="POST"
    )
    try:
        start = time.time()
        r = urllib.request.urlopen(req)
        resp = json.loads(r.read().decode())
        print(f"Status Code: {r.status}")
        print(f"Latency: {round((time.time() - start) * 1000)}ms")
        print(f"Response: {resp['choices'][0]['message']['content']}")
        print(f"Model: {resp.get('model')}")
        print(f"Usage: {resp.get('usage')}")
    except urllib.error.HTTPError as e:
        print(f"Error in OpenAI-Compatible: {e.code} {e.reason} - {e.read().decode()}")
    except Exception as e:
        print(f"Error in OpenAI-Compatible: {e}")

if __name__ == "__main__":
    # Give the gateway server a brief moment to initialize the DB and health checks
    time.sleep(2)
    test_sync()
    test_stream()
    test_openai()
