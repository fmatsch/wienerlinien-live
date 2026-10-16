.PHONY: run test setup record
run:            ## start the server on http://127.0.0.1:4731
	python3 backend/server.py
setup:          ## download static data + build backend/data/network.json (needs ~1 GB temporary disk)
	python3 -I scripts/setup_data.py
test:
	python3 -m unittest discover -s backend/tests -v
record:         ## run the server and keep every raw API answer in recordings/
	WL_RECORD_DIR=recordings python3 backend/server.py
