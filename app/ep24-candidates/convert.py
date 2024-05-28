import json

def map_data(data):
  result = []
  for item in data:
    political_association = item["politicalAssociationName"]
    candidate_name = f"{item['firstName']} {item['lastName']}"
    candidate_number = item['nr']
    found = False
    for entry in result:
      if entry["politicalAssociationName"] == political_association:
        entry["candidates"].append({"name": candidate_name, "number": candidate_number})
        found = True
        break
    if not found:
      result.append({
          "politicalAssociationName": political_association,
          "candidates": [{"name": candidate_name, "number": candidate_number}]
      })
  return result

# Load data from file
with open("candidates.json", "r") as file:
  data = json.load(file)

# Map the data
mapped_data = map_data(data)

# Print the result (optional)
print(json.dumps(mapped_data, indent=2))
