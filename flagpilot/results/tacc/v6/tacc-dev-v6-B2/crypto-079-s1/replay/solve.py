from math import gcd
from Crypto.Util.number import long_to_bytes

with open('challenge/public-key.txt') as f:
    lines = f.read().strip().split('\n')

n1 = int(lines[0].split(': ')[1])
n2 = int(lines[1].split(': ')[1])
n3 = int(lines[2].split(': ')[1])
e = int(lines[3].split(': ')[1])
c = int(lines[4].split(': ')[1])

# Factor using GCD
p = gcd(n1, n2)
q = gcd(n1, n3)
r = gcd(n2, n3)

def decrypt(c, n, p, q, e):
    phi = (p-1)*(q-1)
    d = pow(e, -1, phi)
    return pow(c, d, n)

# Encryption was: c = pow(c, e, n1), then c = pow(c, e, n2), then c = pow(c, e, n3)
# So decrypt in reverse: n3 first, then n2, then n1
c1 = decrypt(c, n3, q, r, e)
c2 = decrypt(c1, n2, p, r, e)
m = decrypt(c2, n1, p, q, e)

flag = long_to_bytes(m)
print(flag.decode())