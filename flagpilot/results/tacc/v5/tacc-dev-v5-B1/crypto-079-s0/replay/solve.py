import re
from math import gcd
from Crypto.Util.number import long_to_bytes
t=open('./challenge/public-key.txt').read()
d={k:int(v) for k,v in re.findall(r'(\w+): (\d+)',t)}
n1,n2,n3,e,c=d['n1'],d['n2'],d['n3'],d['e'],d['c']
p=gcd(n1,n2); q=gcd(n1,n3); r=gcd(n2,n3)
def dec(c,n,pp,qq):
    phi=(pp-1)*(qq-1)
    di=pow(e,-1,phi)
    return pow(c,di,n)
m=dec(c,n3,q,r)
m=dec(m,n2,p,r)
m=dec(m,n1,p,q)
print(long_to_bytes(m).decode())