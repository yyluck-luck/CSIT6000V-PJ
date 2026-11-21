import subprocess

# The encrypted flag from the challenge description
encrypted = 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'

# Use the provided rot13.py script to decode it
result = subprocess.run(['python', './challenge/rot13.py'], input=encrypted, capture_output=True, text=True)
print(result.stdout.strip())