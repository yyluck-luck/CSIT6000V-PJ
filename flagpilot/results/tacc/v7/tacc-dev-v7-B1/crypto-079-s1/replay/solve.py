from math import gcd
from Crypto.Util.number import long_to_bytes

with open('./challenge/public-key.txt') as f:
    lines = f.read().strip().split('\n')

vals = {}
for line in lines:
    k, v = line.split(': ')
    vals[k] = int(v)

n1, n2, n3, e, c = vals['n1'], vals['n2'], vals['n3'], vals['e'], vals['c']

p = gcd(n1, n2)
q = gcd(n1, n3)
r = gcd(n2, n3)

def modinv(a, m):
    return pow(a, -1, m)

def decrypt(c, n, p, q):
    phi = (p-1)*(q-1)
    d = modinv(e, phi)
    return pow(c, d, n)

c2 = decrypt(c, n3, q, r)
c1 = decrypt(c2, n2, p, r)
flag_bytes = decrypt(c1, n1, p, q)
flag = long_to_bytes(flag_bytes)
print(flag.decode())