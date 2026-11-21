import math

def parse(path):
    d={}
    for line in open(path):
        line=line.strip()
        if not line: continue
        k,v=line.split(':')
        d[k.strip()]=int(v.strip())
    return d

pk=parse('challenge/public-key.txt')
n1,n2,n3,e,c=pk['n1'],pk['n2'],pk['n3'],pk['e'],pk['c']

p=math.gcd(n1,n2)
r=math.gcd(n2,n3)
q=math.gcd(n1,n3)

def decrypt(c,n,p,q):
    phi=(p-1)*(q-1)
    d=pow(e,-1,phi)
    return pow(c,d,n)

m=decrypt(c,n3,q,r)
m=decrypt(m,n2,p,r)
m=decrypt(m,n1,p,q)
flag=m.to_bytes((m.bit_length()+7)//8,'big')
print(flag.decode())