import sys
import socket
import threading
import pickle

from Crypto.PublicKey import RSA
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Util import Padding
from Crypto.Random import get_random_bytes
from Crypto.Random.random import getrandbits
from hashlib import sha256
from threading import Thread, Lock
from select import select

class Router:
    ip = None
    port = None
    dirIP = None
    dirPort = None
    priKey = None
    pubKey = None
    

    def __init__(self, ip, port):
        self.ip = ip
        self.port = int(port)
        self.dirIP = 'localhost'
        self.dirPort = 9001
    

    def generateRSAKeys(self):
        # generate keys
        key = RSA.generate(2048)
        pem = key.export_key(format='PEM', passphrase='dees')
        pub = key.publickey()
        pub_pem = pub.export_key(format='PEM')
        # save in state
        self.priKey = pem
        self.pubKey = pub_pem
        return None
    

    def createSocket(self, ip, port):
        ssocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssocket.bind((ip, port))
        ssocket.listen()
        return ssocket


    def connectSocket(self, ip, port):
        ssocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssocket.connect((ip, port))
        return ssocket
    

    def register(self):
        ssocket = self.connectSocket(self.dirIP, self.dirPort)
        data = {
            'ip': self.ip,
            'port': self.port,
            'pubKey': self.pubKey
        }
        msg = b'R' + pickle.dumps(data)
        ssocket.send(msg)
        ssocket.close()
        return None
    

    def decryptRSA(self, data):
        priKey = RSA.importKey(self.priKey, passphrase='dees')
        cipher = PKCS1_OAEP.new(priKey)
        plainText = cipher.decrypt(data)
        return plainText.decode()


    def encryptAES(self, hsk, nonce, data):
        key = bytes.fromhex(hsk.hexdigest())
        nonce = nonce.digest()[0:8]
        cipher = AES.new(key, AES.MODE_CTR, nonce=nonce)
        ct_bytes = cipher.encrypt(data)
        return ct_bytes


    def decryptAES(self, hsk, nonce, data):
        key = bytes.fromhex(hsk.hexdigest())
        nonce = nonce.digest()[0:8]
        cipher = AES.new(key, AES.MODE_CTR, nonce=nonce)
        return cipher.decrypt(data)


    def padData(self, length, data):
        return data.ljust(length, b'\0')


    def unpadData(self, data):
        return data.rstrip(b'\0')


    def closeSockets(self, sockets):
        for x in sockets:
            x.close()
        return None


    def handleClient(self, connection, address):
        print('New connection - {}'.format(address))
        p = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485
        g = 2
        y = getrandbits(1024)
        hsk = None
        hnonce = None
        circID = None
        nextRouter = None
        nextCircID = None
        sockets = [connection]
        exitRouter = False
        
        while True:
            try:
                r, w, e = select(sockets, [], [])
            except ValueError:
                sockets.remove(nextRouter)
            
            # -->
            if connection in r:
                try:
                    msg = connection.recv(512)
                except ConnectionResetError:
                    break
                # if len(msg) <= 0: continue
                cmd = msg[2:3]
                data = msg[3:]
                # create
                if cmd == b'\x01':
                    circID = msg[0:2]
                    # unpad
                    data = self.unpadData(data)
                    nonce = data[0:8]
                    hnonce = sha256(nonce)
                    egx = data[8:]
                    # calc shared key
                    gx = int(self.decryptRSA(egx))
                    sk = pow(gx, y, p)
                    hsk = sha256(str(sk).encode())
                    # calc gy to send back
                    gy = pow(g, y, p)
                    data = self.padData(509, str(gy).encode())
                    # send back to sender
                    msg = circID + b'\x02' + data
                    connection.send(msg)
                # relay
                elif cmd == b'\x03':
                    # decrypt layer
                    relayHeader = self.decryptAES(hsk, hnonce, data)
                    streamID = relayHeader[0:2]
                    digest = relayHeader[2:8]
                    length = int.from_bytes(relayHeader[8:10], sys.byteorder)
                    cmd = relayHeader[10:11]
                    data = relayHeader[11:length+11]
                    # check if digest is valid
                    if hsk.digest()[0:6] == digest:
                        # update digest
                        hsk.update(relayHeader)
                        hnonce.update(relayHeader)
                        # extend
                        if cmd == b'\x04':
                            # send create to next router
                            nonce = data[0:8]
                            router = self.unpadData(data[8:29]).decode()
                            ip, port = tuple(router.split(':'))
                            try:
                                nextRouter = self.connectSocket(ip, int(port))
                                sockets.append(nextRouter)
                            except Exception as inst:
                                print(inst)
                            # send create
                            nextCircID = get_random_bytes(2)
                            ex = data[29:]
                            data = nonce + ex
                            paddedData = self.padData(509, data)
                            msg = (nextCircID
                                   + b'\x01'
                                   + paddedData)
                            nextRouter.send(msg)
                        # begin
                        elif cmd == b'\x06':
                            address = data.decode()
                            ip, port = tuple(address.split(':'))
                            # create tcp connection
                            nextRouter = self.connectSocket(ip, int(port))
                            sockets.append(nextRouter)
                            exitRouter = True
                            # build msg
                            streamID = get_random_bytes(2)
                            digest = hsk.digest()[0:6]
                            length = get_random_bytes(2)
                            relayHeader = (streamID
                                           + digest
                                           + length
                                           + b'\x07'
                                           + self.padData(498, b''))
                            cipherText = self.encryptAES(hsk, hnonce, relayHeader)
                            # send relay back to connection
                            msg = (circID
                                   + b'\x03'
                                   + cipherText)
                            connection.send(msg)
                        # data
                        elif cmd == b'\x08':
                            request = self.unpadData(data)
                            # send request
                            nextRouter.send(request)
                        # end
                        elif cmd == b'\x09':
                            # build msg
                            streamID = get_random_bytes(2)
                            digest = hsk.digest()[0:6]
                            length = get_random_bytes(2)
                            relayHeader = (streamID
                                           + digest
                                           + length
                                           + b'\x09'
                                           + self.padData(498, b''))
                            cipherText = self.encryptAES(hsk, hnonce, relayHeader)
                            # send relay back to connection
                            msg = (circID
                                   + b'\x03'
                                   + cipherText)
                            connection.send(msg)
                            # close nextRouter
                            nextRouter.close()
                    else:
                        # forward to next router
                        msg = (nextCircID
                               + b'\x03'
                               + relayHeader)
                        nextRouter.send(msg)
            
            # <--
            if nextRouter in r:
                if exitRouter:
                    try:
                        msg = nextRouter.recv(498)
                    except OSError:
                        nextRouter.close()
                        continue
                    streamID = get_random_bytes(2)
                    digest = hsk.digest()[0:6]
                    length = len(msg).to_bytes(2, sys.byteorder)
                    paddedData = self.padData(498, msg)
                    relayHeader = (streamID
                                   + digest
                                   + length
                                   + b'\x08'
                                   + paddedData)
                    cipherText = self.encryptAES(hsk, hnonce, relayHeader)
                    # send relay back to connection
                    msg = (circID
                           + b'\x03'
                           + cipherText)
                    connection.send(msg)
                else:
                  try:
                      msg = nextRouter.recv(512)
                  except ConnectionResetError:
                      break
                  # if len(msg) <= 0: continue
                  cmd = msg[2:3]
                  data = msg[3:]
                  # created
                  if cmd == b'\x02':
                      # unpad
                      gy = self.unpadData(data)
                      # create relay message
                      streamID = get_random_bytes(2)
                      digest = hsk.digest()[0:6]
                      length = len(gy).to_bytes(2, sys.byteorder)
                      relayHeader = (streamID
                                     + digest
                                     + length
                                     + b'\x05'
                                     + self.padData(498, gy))
                      cipherText = self.encryptAES(hsk, hnonce, relayHeader)
                      # send relay back to connection
                      msg = (circID
                             + b'\x03'
                             + cipherText)
                      connection.send(msg)
                  # relay
                  elif cmd == b'\x03':
                      # add layer
                      cipherText = self.encryptAES(hsk, hnonce, data)
                      # send relay back to connection
                      msg = (circID
                             + b'\x03'
                             + cipherText)
                      connection.send(msg)
        
        self.closeSockets(sockets)
        print('Connection {} closed.'.format(address))
        return None


    def listen(self):
        # create server socket
        ip = self.ip
        port = self.port
        ssocket = self.createSocket(ip, port)
        print('Server is listening on {}:{}'.format(ip, port))
        while True:
            connection, address = ssocket.accept()
            thread = threading.Thread(target=self.handleClient, args=(connection, address))
            thread.start()
            print('Number of connections: {}'.format(threading.activeCount() - 1))
        ssocket.close()
        return None
  

def main(ip, port):
    # create router obj
    router = Router(ip, port)
    # create RSA keypair
    router.generateRSAKeys()
    # register with directory
    router.register()
    # start listening
    router.listen()
    return None


if __name__ == '__main__':
    ip = sys.argv[1]
    port = sys.argv[2]
    main(ip, port)
