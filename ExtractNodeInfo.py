import zipfile, re, csv
import zipfile, struct

with zipfile.ZipFile(r"E:\Github\MCP_Geostudio\MCP\Metro-Center-slope-25ft.gsz", "r") as z:
    raw = z.read("mesh_3.ply")

# Find end of header
header_end = raw.find(b"end_header\n") + len(b"end_header\n")
header = raw[:header_end].decode("utf-8")
print(header)

data = raw[header_end:]

# Skip version element: 1 entry × (ushort + ushort) = 4 bytes
offset = 4

# Read 201 nodes: each is (double x, double y, double z) = 24 bytes
nodes = []
for i in range(201):
    x, y, z = struct.unpack_from('<ddd', data, offset)
    offset += 24
    nodes.append((i+1, round(x, 4), round(y, 4)))

# Print first 20 and last 5
for node_id, x, y in nodes[:20]:
    print(f"Node {node_id:3d}: x={x:10.4f}  y={y:10.4f}")
print("...")
for node_id, x, y in nodes[-5:]:
    print(f"Node {node_id:3d}: x={x:10.4f}  y={y:10.4f}")

print(f"\nTotal nodes: {len(nodes)}")
print(f"X range: {min(n[1] for n in nodes):.2f} to {max(n[1] for n in nodes):.2f}")
print(f"Y range: {min(n[2] for n in nodes):.2f} to {max(n[2] for n in nodes):.2f}")

with open("node_coordinates_H15.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["Node", "X_ft", "Y_ft"])
    for node_id, x, y in nodes:
        writer.writerow([node_id, x, y])
print("Saved node_coordinates_H15.csv")