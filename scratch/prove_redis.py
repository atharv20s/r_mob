import requests, redis

BASE = 'http://127.0.0.1:8000/api/v1'

# 1. Login
r = requests.post(f'{BASE}/auth/login', data={'username': 'admin@route.com', 'password': 'adminpassword'})
token = r.json()['access_token']
print(f'[1] Login: HTTP {r.status_code}')

# 2. Chat (cache miss)
r2 = requests.post(f'{BASE}/chat', json={'prompt': 'What is Redis?'},
                   headers={'Authorization': f'Bearer {token}'})
d2 = r2.json()
print(f'[2] Chat 1 (first ask):  HTTP {r2.status_code}  cached={d2["cached"]}')

# 3. Same prompt again (cache hit)
r3 = requests.post(f'{BASE}/chat', json={'prompt': 'What is Redis?'},
                   headers={'Authorization': f'Bearer {token}'})
d3 = r3.json()
print(f'[3] Chat 2 (same prompt): HTTP {r3.status_code}  cached={d3["cached"]}')

# 4. Logout (blacklists JWT in Redis)
r4 = requests.post(f'{BASE}/auth/logout', headers={'Authorization': f'Bearer {token}'})
print(f'[4] Logout: HTTP {r4.status_code}')

# 5. Dump ALL keys from Docker Redis (port 6380)
client = redis.Redis(host='127.0.0.1', port=6380, decode_responses=True)
keys = client.keys('*')
print()
print('=== DOCKER REDIS KEYS (port 6380) ===')
for k in sorted(keys):
    t = client.type(k)
    ttl = client.ttl(k)
    print(f'  {k:<50}  type={t:<8}  ttl={ttl}s')

print()
print(f'Total keys in Docker Redis: {len(keys)}')
