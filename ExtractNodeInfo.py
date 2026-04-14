import sys, os, zipfile, struct, re, csv, glob

def process_gsz(gsz_path):
    print(f"\nProcessing {os.path.basename(gsz_path)}")
    try:
        with zipfile.ZipFile(gsz_path, "r") as z:
            ply_files = [f for f in z.namelist() if f.endswith('.ply')]
            if not ply_files:
                print("  Error: No .ply file found in the archive.")
                return
            
            target_ply = next((p for p in ply_files if "Rainfall Simulation" in p or "SEEP" in p), None)
            if not target_ply:
                target_ply = ply_files[0]
                
            print(f"  Reading mesh: {target_ply}")
            raw = z.read(target_ply)
    except Exception as e:
        print(f"  Error reading GSZ: {e}")
        return

    header_end = raw.find(b"end_header\n") + len(b"end_header\n")
    header = raw[:header_end].decode("utf-8")

    match = re.search(r'element (?:node|vertex) (\d+)', header)
    num_nodes = int(match.group(1)) if match else 201

    data = raw[header_end:]
    offset = 4
    nodes = []
    for i in range(num_nodes):
        try:
            x, y, z_val = struct.unpack_from('<ddd', data, offset)
            offset += 24
            nodes.append((i+1, round(x, 4), round(y, 4)))
        except struct.error:
            break

    dir_name = os.path.dirname(gsz_path)
    base_name = os.path.splitext(os.path.basename(gsz_path))[0]
    out_csv = os.path.join(dir_name, f"{base_name}_nodes.csv")

    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Node", "X_ft", "Y_ft"])
        for node_id, x, y in nodes:
            writer.writerow([node_id, x, y])

    print(f"  Saved {out_csv}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python ExtractNodeInfo.py <path/to/file.gsz OR directory>")
        sys.exit(1)

    target = sys.argv[1]
    if os.path.isfile(target):
        process_gsz(target)
    elif os.path.isdir(target):
        print(f"Scanning directory: {target}")
        files = glob.glob(os.path.join(target, "**", "*.gsz"), recursive=True)
        if not files:
            print(f"  No .gsz files found.")
        for f in files:
            process_gsz(f)
    else:
        print(f"Error: {target} not found.")

if __name__ == "__main__":
    main()