import subprocess
result = subprocess.run(['python3', './challenge/rot13.py'], input='cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}', capture_output=True, text=True)
print(result.stdout.strip())