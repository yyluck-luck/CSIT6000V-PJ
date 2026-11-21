from math import gcd
from Crypto.Util.number import long_to_bytes

with open('./challenge/public-key.txt') as f:
    lines = f.read().strip().split('\n')

n1 = int(lines[0].split(': ')[1])
n2 = int(lines[1].split(': ')[1])
n3 = int(lines[2].split(': ')[1])
e = int(lines[3].split(': ')[1])
c = int(lines[4].split(': ')[1])

p = gcd(n1, n2)
q = gcd(n1, n3)
r = gcd(n2, n3)

def modinv(a, m):
    return pow(a, -1, m)

def decrypt(c, n, p, q):
    phi = (p-1)*(q-1)
    d = modinv(e, phi)
    return pow(c, d, n)

c1 = decrypt(c, n3, q, r)
c2 = decrypt(c1, n2, p, r)
c3 = decrypt(c2, n1, p, q)

flag = long_to_bytes(c3)
print(flag.decode())