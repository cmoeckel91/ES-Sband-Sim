'''
Update the FTDI standard buffer timeout from 16 ms to 1 ms. 
Linux: 
dmesg | grep ttyUSB 
echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
'''

import serial
import time
import struct
from binascii import crc32
import os
from datetime import datetime
import logging 


# User input 
# ---------------------------------------------------# 

#PORT    = 'COM7' # Windows 
PORT = '/dev/ttyUSB0'# Linux/osX 
BAUD    = 3000000
TIMEOUT = 0.01  

HEADER      = b'ESUP'
MODULE_ID   = 0x2019  # Module ID used for GW
# ---------------------------------------------------# 


verbose = False
loguart = True

# Bytes and Byte sizes 
START_BYTE  = 0x45
HEADER_LEN  = 14
CRC_LEN     = 4
FILE_SIZE   = 4 
AUX_DATA_LEN = 2 
AUX_H_ID    = 4 
AUX_PKT_N   = 4 
AUX_HEADER  = AUX_DATA_LEN+AUX_H_ID+AUX_PKT_N
MAX_CHUNK   = 1472
MAX_BUFFER  = HEADER_LEN + AUX_HEADER + MAX_CHUNK + CRC_LEN 

# Manual (max of 1472 bytes)+ aux header (10 bytes), header (14 bytes) + CRC (4)


CMD_GET         = 0x0100
CMD_F_DEL       = 0x0104
CMD_F_CREATE    = 0x0106
CMD_F_WRITE     = 0x0107 
CMD_MODE        = 0x0111 
CMD_RESULT      = 0x0114 

# Response 
RES_ACK     = 0x0005
RES_BUSY    = 0x0007  
RES_NULL    = 0x0000

# Global variable 
global Handler_Id, File_Sessions, path2data, Filename_Map

File_Sessions = {}  # handler_id → FileTransferSession
Handler_Id = 0 
path2data = './Data/'
Filename_Map = set()

try:
    os.makedirs(path2data, exist_ok=True)
except OSError as e:
    print(f"Error creating directory: {e}")


logging.basicConfig(
    filename='uart_debug.log',
    filemode='w',
    format='%(asctime)s %(levelname)s: %(message)s',
    level=logging.DEBUG
)

# Disable the root logger
logging.getLogger().disabled = not loguart


def pad_to_multiple_of_16(data):
    padding_len = (16 - (len(data) % 16)) % 16
    return data + bytes([0x00] * padding_len)

def compute_crc(data):
    return crc32(data) & 0xFFFFFFFF

# Handles incoming packages 
class ParsedPacket:
    def __init__(self, raw_bytes):
        if len(raw_bytes) < HEADER_LEN + CRC_LEN:
            raise ValueError(f"Packet {len(raw_bytes)} too short")
        self.raw = raw_bytes
        #logging.debug(f"Received raw bytes: {raw_bytes.hex()}")
        logging.debug(f"Received raw bytes: " + ' '.join(f'{b:02X}' for b in raw_bytes))
        

        self.header = self.raw[:HEADER_LEN]
        self.parse_header()

        if self.response == RES_ACK: 
            logging.debug(f"✅ ACK received for cmd {self.cmd:04X}")
            if verbose: print(f"✅ ACK received for cmd {self.cmd:04X}")
            return 

        self.data = self.raw[HEADER_LEN:HEADER_LEN+self.length]
        #self.received_crc = self.raw[HEADER_LEN+self.length:HEADER_LEN+self.length+CRC_LEN]
        self.received_crc = self.raw[-CRC_LEN:]
        self.calculated_crc = compute_crc(self.header+self.data).to_bytes(CRC_LEN, 'little')
        self.valid = self.calculated_crc == self.received_crc 

        logging.debug(f"CRC calculated - received: {self.calculated_crc.hex()} - {self.received_crc.hex()}")

        if not self.valid: 
            # print(f'Length {len(raw_bytes)-HEADER_LEN} vs Header len {self.length}')
            # print("📦 Raw packet invalid:", ' '.join(f'{b:02X}' for b in self.raw[:24]))
            # print("📦 Raw packet invalid:", ' '.join(f'{b:02X}' for b in self.raw[-48:]))
            # print("📦 Received CRC:", ' '.join(f'{b:02X}' for b in self.received_crc))
            # print("📦 Calculated CRC:", ' '.join(f'{b:02X}' for b in self.calculated_crc))
            raise ValueError("CRC mismatch")
        self.parse_payload()

    def parse_header(self):
        hdr = struct.unpack('<4sHHHHH', self.header)
        self.magic, self.module_id, self.length, self.response, self.cmd, self.cmd_type = hdr
        self.valid = self.magic == b'ESUP' 
        

    def parse_payload(self):
        # Dispatch specific parsing based on CMD & Type
        parser = payload_parsers.get((self.cmd, self.cmd_type))
        if parser:
            parser(self)
        else:
            print(f"⚠️ No handler for cmd=0x{self.cmd:04X} type=0x{self.cmd_type:04X}")

    def __repr__(self):
        return f"<Packet cmd=0x{self.cmd:04X} type=0x{self.cmd_type:04X} valid={self.valid}>"

class FileTransferSession:
    def __init__(self, filename, total_size):
        self.filename = filename
        self.total_size = total_size
        self.chunks = {}
        self.received_chunks = set()  # Keep track of received chunk numbers
        self.received = 0
        #print(f'Total bytes {self.total_size}')


    def add_chunk(self, offset, data):
        self.chunks[offset*MAX_CHUNK] = data
        self.received += len(data) 
        self.received_chunks.add(offset)

    
    def is_complete(self):
        if verbose: print(f'Progress {self.received/self.total_size*100}%')
        return self.received >= self.total_size
    
    def write_to_disk(self):
        with open(path2data + self.filename, 'wb') as f:
            for offset in sorted(self.chunks):
                f.seek(offset)
                f.write(self.chunks[offset])


def acknowledge_cmd(packet: ParsedPacket,busy=False): 
    # === Define header fields ===
    pay_len = 0 

    if busy: resp = RES_BUSY
    else: resp = RES_ACK


    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                resp,
                                packet.cmd,
                                packet.cmd_type)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct 

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little') 

    final_response = pad_to_multiple_of_16(response + crc_bytes) 
    ser.write(final_response)
    ser.flush()
    
    logging.debug(f"✅ ACK {resp} sent for cmd {packet.cmd:04X}")
    if verbose:  print(f"✅ ACK sent for cmd {packet.cmd:04X}")



def send_simple_report(packet: ParsedPacket):    
    # === Define header fields ===
    cmd_type   = 0x0049          # Command type: SIMPLE_REPORT

    # === Define payload fields ===
    status      = 0x02           # 1 byte
    flags       = 0x00           # 1 byte
    reserved    = 0x0000         # 2 bytes
    cpu_temp    = 23.6           # 4-byte float
    firmware    = 0xD8270000     # 4-byte unsigned int

    # === Pack the payload (big-endian) ===
    payload = struct.pack('<BBHfI',
                          status,
                          flags,
                          reserved,
                          cpu_temp,
                          firmware)

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)    

    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_GET,
                                cmd_type)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')
    
    final_response = pad_to_multiple_of_16(response + crc_bytes) 
    ser.write(final_response)
    ser.flush()
    
    logging.debug("📤 ✅ Simple report sent: " +  final_response.hex(' ').upper())



def delete_file_confirm(packet: ParsedPacket):
    # === Define payload fields ===
    status      = 0x00           # A OK

    # === Pack the payload (big-endian) ===
    payload = struct.pack('<B',status)

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)  

    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_F_DEL,
                                RES_NULL)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct  + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')

    final_response = pad_to_multiple_of_16(response + crc_bytes )
    ser.write(final_response)
    ser.flush()
    
    logging.debug("📤 Delete file confirmed:" +  final_response.hex(' ').upper())


def create_file(packet: ParsedPacket): 
    global Handler_Id, File_Sessions, Filename_Map
    
    filename_len = packet.length - FILE_SIZE  
    filename = packet.data[:filename_len].decode('ascii').rstrip('\x00')
    logging.debug(f"File session created {filename}")

    # if filename in Filename_Map:
    #     logging.debug(f"⚠️ Duplicate filename {filename}, ignoring.")
    #     acknowledge_cmd(packet)
    #     return
    # else: 
    #     Filename_Map.add(filename)


    file_size = (int.from_bytes(packet.data[-FILE_SIZE:], 'little')) 

    if file_size <= 0: 
        return 
    else: 
        file_size -= 132 

    Handler_Id += 1
    File_Sessions[Handler_Id] = FileTransferSession(filename, file_size)

    if verbose: print(f"📁 File session created: {filename} with handler {Handler_Id:#08x} and size {file_size:#08x}")
    
    logging.debug(f"📁 File session created: {filename} with handler {Handler_Id:#08x} and size {file_size:#08x}")

    # Send out confirmation to command 
    acknowledge_cmd(packet)



def create_file_confirm(packet: ParsedPacket):
    global Handler_Id

    # === Define payload fields ===
    status      = 0x00           # A OK
    filehandle  = Handler_Id     # A OK

    # === Pack the payload (big-endian) ===
    payload = struct.pack('<Bi',status,filehandle)

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)  

    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_F_CREATE,
                                RES_NULL)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct  + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')
    full_response = response + crc_bytes 
    
    final_response = pad_to_multiple_of_16(response + crc_bytes )
    ser.write(final_response)
    ser.flush()
    
    if verbose: print("📤 Create file confirmed: ", ' '.join(f'{b:02X}' for b in response))
    logging.debug("📤 Create file confirmed:" +  final_response.hex(' ').upper())


def write_file(packet: ParsedPacket): 

    global File_Sessions 
    data_len, h_id, pkt_num = struct.unpack('<H i I', packet.data[:AUX_HEADER])
    session     = File_Sessions.get(h_id)
    
    if not session:
        if verbose: print(f"⚠️ Unknown file handler: 0x{h_id:08X}")
        return

    if pkt_num in session.received_chunks:
        acknowledge_cmd(packet)
        logging.debug(f"🔁 Chunk {chunk_number} already received, skipped writing.")
        print(f"🔁 Chunk {chunk_number} already received, skipped writing.")
        return

    session.add_chunk(pkt_num, packet.data[AUX_HEADER:])

    if session.is_complete():
        write_file_confirm(packet)
        session.write_to_disk()
        print(f"✅ File {session.filename} transferred")
        del File_Sessions[h_id]

    # Send out confirmation to command 
    acknowledge_cmd(packet)

def write_file_confirm(packet: ParsedPacket):
    # === Define payload fields ===
    status      = 0x00           # A OK

    # === Pack the payload (big-endian) ===
    payload = struct.pack('<B',status)

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)  

    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_F_WRITE,
                                RES_NULL)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct  + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')

    final_response = pad_to_multiple_of_16(response + crc_bytes )
    ser.write(final_response)
    ser.flush()
    
    if verbose: print("📤 Write file confirmed: ", ' '.join(f'{b:02X}' for b in response))
    logging.debug("📤 Write file chunk confirmed:" +  final_response.hex(' ').upper())

def mode_confirm(packet: ParsedPacket):
    # === Define payload fields ===
    status      = 0x00           # A OK

    # === Pack the payload (big-endian) ===
    payload = struct.pack('<B',status)

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)  

    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_MODE,
                                RES_NULL)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct  + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')

    final_response = pad_to_multiple_of_16(response + crc_bytes )
    ser.write(final_response)
    ser.flush()

    if verbose: print("📤 Mode confirmed:", ' '.join(f'{b:02X}' for b in response))
    logging.debug("📤 Mode confirmed:" +  final_response.hex(' ').upper())

def request_data(packet: ParsedPacket):
    data_type = int.from_bytes(packet.data, byteorder='little')
    req_data, num_bytes = request_parsers.get(data_type, (None, None))
    
    if req_data is None:
        print(f"❌ Unknown data_type requested: {data_type:#06x}")
        return

    # === Define payload fields ===
    status      = 0x00           # A OK

    # === Pack the payload (little-endian) ===
    payload = struct.pack('<B',status) + req_data.to_bytes(num_bytes, 'little')

    # === Calculate length field: payload size - 1 ===
    pay_len = len(payload)  


    # === Pack the full header ===
    header_struct = struct.pack('<4sHHHHH',
                                packet.magic,
                                packet.module_id,
                                pay_len,
                                RES_NULL,
                                CMD_MODE,
                                RES_NULL)

    # === Final packet is header + payload (CRC should be appended later) ===
    response = header_struct  + payload

    crc_bytes = compute_crc(response).to_bytes(CRC_LEN, byteorder='little')

    final_response = pad_to_multiple_of_16(response + crc_bytes )
    ser.write(final_response)
    ser.flush()
    if verbose: print("📤 Requested data sent: ", ' '.join(f'{b:02X}' for b in response))
    logging.debug("📤 Requested data sent: " +  final_response.hex(' ').upper())


payload_parsers = {
    (CMD_GET, 0x0049): acknowledge_cmd,
    (CMD_GET, 0x0040): acknowledge_cmd,
    (CMD_RESULT, 0x0001): send_simple_report,
    (CMD_F_DEL, 0x0000): acknowledge_cmd,
    (CMD_RESULT, CMD_F_DEL): delete_file_confirm,
    (CMD_F_CREATE, 0x0000): create_file,
    (CMD_RESULT, CMD_F_CREATE): create_file_confirm,
    (CMD_F_WRITE, 0x0000): write_file,
    (CMD_RESULT, CMD_F_WRITE): write_file_confirm,
    (CMD_MODE, 0x0000): acknowledge_cmd,
    (CMD_RESULT, CMD_MODE): mode_confirm,
    (CMD_RESULT, CMD_GET): request_data,
}


request_parsers = {
    (0x0040): (0x03,2),  # Symbol rate (3 bytes)
    (0x0047): (0x0BB,3),  # Transmission delay (2 bytes)
    (0x0049): ()
}

def main():
    global ser
    ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)

    while True:
        header_bytes = bytearray()
        while len(header_bytes) < HEADER_LEN + CRC_LEN:
            header_bytes += ser.read(HEADER_LEN + CRC_LEN - len(header_bytes))

        while True:
            idx = header_bytes.find(START_BYTE)
            if idx == 0:
                break  # Found it
            elif idx == -1:
                header_bytes = bytearray()
                break  # Go back to outer loop to refill from scratch
            else:
                header_bytes = header_bytes[idx:]
                header_bytes += ser.read(HEADER_LEN + CRC_LEN - len(header_bytes))

        try: length = int.from_bytes(header_bytes[6:8], byteorder='little')   # actual length
        except: break  

        remaining_bytes = ser.read(length)

        if len(remaining_bytes) < (length) or len(remaining_bytes) > MAX_BUFFER:
            if verbose: print(f"⚠️ Incomplete packet {len(remaining_bytes)}, expected {length + CRC_LEN}. skipping.")
            if verbose: (" Header:", ' '.join(f'{b:02X}' for b in header_bytes))
            continue
    
        # This shouldn't be necessary but here we are 
        if len(header_bytes + remaining_bytes)  < HEADER_LEN + CRC_LEN:
            continue 

        # Step 5: Construct and parse full packet
        try:
            pkt = ParsedPacket(header_bytes + remaining_bytes)
        except Exception as e:
            #print(f"❌ Failed to parse packet: {e}")
            continue

        if not pkt.valid:
            print("❌ CRC mismatch. Discarding packet.")
            continue

        time.sleep(0.01)


if __name__ == '__main__':
    main()