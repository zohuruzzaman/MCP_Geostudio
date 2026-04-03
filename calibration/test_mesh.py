import zipfile, struct
import numpy as np

def test_read_mesh():
    gsz_path = r"E:\Github\MCP_Geostudio\calibration\Metro-Center_cal.gsz"
    ply_path = "Rainfall Simulation/Mesh.ply"
    
    with zipfile.ZipFile(gsz_path, 'r') as z:
        content = z.read(ply_path)
        
    header_end_idx = content.find(b"end_header\n")
    header_len = header_end_idx + 11
    header = content[:header_len].decode('utf-8', errors='replace')
    
    print("--- HEADER ---")
    print(header)
    
    n_nodes = 0
    for line in header.splitlines():
        if line.startswith("element node"):
            n_nodes = int(line.split()[-1])
            
    print(f"Nodes: {n_nodes}")
    
    offset = header_len
    has_version = "element version" in header
    if has_version:
        offset += 4
        
    xs, ys = [], []
    for _ in range(min(n_nodes, 5)):
        x, y, z = struct.unpack('<ddd', content[offset:offset+24])
        xs.append(x)
        ys.append(y)
        offset += 24
        
    print("First 5 nodes:")
    for i in range(len(xs)):
        print(f"  {xs[i]:.2f}, {ys[i]:.2f}")

if __name__ == "__main__":
    test_read_mesh()
