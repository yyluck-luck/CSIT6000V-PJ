import subprocess
r = subprocess.run(['openssl', 'req', '-in', './challenge/readmycert.csr', '-noout', '-text'], capture_output=True, text=True)
print(r.stdout)