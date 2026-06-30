import os
import sys
import json
from bson import json_util
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.mongo_adapters import ServerAllianceAdapter, _get_db_main
import logging

logging.basicConfig(level=logging.INFO)

db = _get_db_main()
docs = list(db[ServerAllianceAdapter.COLL].find({}))

with open("server_alliances_dump.json", "w", encoding="utf-8") as f:
    f.write(json_util.dumps(docs, indent=2))
