import os

# Read the table from challenge directory
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'challenge', 'table.txt')) as f:
    lines = f.readlines()

# Parse the table to build the Vigenere lookup
# The table is a standard Vigenere table where table[row][col] = (row + col) mod 26
# For decryption: plaintext = (ciphertext - key) mod 26

ciphertext = "UFJKXQZQUNB"
key = "SOLVECRYPTO"

# Verify table structure by reading it
# Find the header row with letters
letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
table = {}
for line in lines:
    line = line.strip()
    if line.startswith('A |') or line.startswith('B |') or line.startswith('C |') or line.startswith('D |') or line.startswith('E |') or line.startswith('F |') or line.startswith('G |') or line.startswith('H |') or line.startswith('I |') or line.startswith('J |') or line.startswith('K |') or line.startswith('L |') or line.startswith('M |') or line.startswith('N |') or line.startswith('O |') or line.startswith('P |') or line.startswith('Q |') or line.startswith('R |') or line.startswith('S |') or line.startswith('T |') or line.startswith('U |') or line.startswith('V |') or line.startswith('W |') or line.startswith('X |') or line.startswith('Y |') or line.startswith('Z |'):
        row_letter = line[0]
        cells = line.split('|')[1].strip().split()
        table[row_letter] = cells

# Decrypt using the table: for each ciphertext char C and key char K,
# find the column where table[K][col] == C, that column index is the plaintext
plaintext = ""
for i, c in enumerate(ciphertext):
    k = key[i % len(key)]
    row = table[k]
    p_idx = row.index(c)
    plaintext += letters[p_idx]

print(f"picoCTF{{{plaintext}}}")