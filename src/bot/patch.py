import sys

with open(r'db\mongo_adapters.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    "'giftcode': str(_id),\n                        'date': d.get('date'),",
    "'giftcode': str(_id),\n                        'giftcode_original': d.get('giftcode_original', str(_id)),\n                        'date': d.get('date'),"
)
content = content.replace(
    "'giftcode': doc.get('_id'),\n                    'date': doc.get('date'),",
    "'giftcode': doc.get('_id'),\n                    'giftcode_original': doc.get('giftcode_original', doc.get('_id')),\n                    'date': doc.get('date'),"
)

with open(r'db\mongo_adapters.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Patched successfully')
