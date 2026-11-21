from math import gcd
from Crypto.Util.number import long_to_bytes

with open('./challenge/public-key.txt') as f:
    txt = f.read()
vals = {}
for line in txt.strip().splitlines():
    k, v = line.split(':')
    vals[k.strip()] = int(v.strip())
n1, n2, n3, e, c = vals['n1'], vals['n2'], vals['n3'], vals['e'], vals['c']

p = gcd(n1, n2)
q = n1 // p
r = n2 // p

def dec(c, n, a, b):
    phi = (a-1)*(b-1)
    d = pow(e, -1, phi)
    return pow(c, d, n)

# reverse sequential: c3 -> c2 -> c1 -> m
c2 = dec(c, n3, q, r)
c1 = dec(c2, n2, p, r)
m = dec(c1, n1, p, q)
print(long_to_bytes(m).decode())