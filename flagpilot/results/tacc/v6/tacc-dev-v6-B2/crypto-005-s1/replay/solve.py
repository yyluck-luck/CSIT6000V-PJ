import subprocess, sys

# The ciphertext is given in the challenge description
ciphertext = "cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}"

# Read the ROT13 script from the challenge file
with open("./challenge/rot13.py", "r") as f:
    script = f.read()

# Run the script with the ciphertext as input
result = subprocess.run([sys.executable, "-c", script], input=ciphertext, capture_output=True, text=True)
print(result.stdout.strip())