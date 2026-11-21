import math
from Crypto.Util.number import long_to_bytes

with open('./challenge/public-key.txt') as f:
    txt=f.read()
vals={}
for line in txt.splitlines():
    k,v=line.split(': ')
    vals[k]=int(v)
n1,n2,n3,e,c=vals['n1'],vals['n2'],vals['n3'],vals['e'],vals['c']

p=math.gcd(n1,n2)
q=math.gcd(n1,n3)
r=math.gcd(n2,n3)

factors={n1:(p,q), n2:(p,r), n3:(q,r)}

def dec(c,n):
    a,b=factors[n]
    phi=(a-1)*(b-1)
    d=pow(e,-1,phi)
    return pow(c,d,n)

m=dec(c,n3)
m=dec(m,n2)
m=dec(m,n1)
print(long_to_bytes(m).decode())