from math import gcd
from Crypto.Util.number import long_to_bytes
d={}
for line in open('./challenge/public-key.txt'):
    k,v=line.split(':')
    d[k.strip()]=int(v.strip())
n1,n2,n3,e,c=d['n1'],d['n2'],d['n3'],d['e'],d['c']
p=gcd(n1,n2);q=gcd(n1,n3);r=gcd(n2,n3)
phi1=(p-1)*(q-1);phi2=(p-1)*(r-1);phi3=(q-1)*(r-1)
d1=pow(e,-1,phi1);d2=pow(e,-1,phi2);d3=pow(e,-1,phi3)
c2=pow(c,d3,n3);c1=pow(c2,d2,n2);m=pow(c1,d1,n1)
print(long_to_bytes(m).decode())