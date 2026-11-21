import os

# Read the table from challenge directory
table_path = os.path.join("./challenge", "table.txt")
with open(table_path, "r") as f:
    lines = f.readlines()

# Parse the table
letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
table = {}
for line in lines:
    line = line.strip()
    if line.startswith("A |") or line.startswith("B |") or line.startswith("C |") or line.startswith("D |") or line.startswith("E |") or line.startswith("F |") or line.startswith("G |") or line.startswith("H |") or line.startswith("I |") or line.startswith("J |") or line.startswith("K |") or line.startswith("L |") or line.startswith("M |") or line.startswith("N |") or line.startswith("O |") or line.startswith("P |") or line.startswith("Q |") or line.startswith("R |") or line.startswith("S |") or line.startswith("T |") or line.startswith("U |") or line.startswith("V |") or line.startswith("W |") or line.startswith("X |") or line.startswith("Y |") or line.startswith("Z |"):
        row_letter = line[0]
        cells = line.split("|")[1].strip().split()
        table[row_letter] = cells

# Encrypted text and key from the challenge description
enc = "UFJKXQZQUNB"
key = "SOLVECRYPTO"

# Decrypt using the table: find P such that table[K][P] = E
plain = []
for i, e in enumerate(enc):
    k = key[i % len(key)]
    row = table[k]
    p = row.index(e)
    plain.append(letters[p])

print("picoCTF{" + "".join(plain) + "}")