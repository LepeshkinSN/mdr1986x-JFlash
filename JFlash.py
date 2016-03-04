from __future__ import division

r"""
JFlash.py -- GDB script for Milandr MCU 1986x flashing with J-Link
http://github.com/in4lio/mdr1986x-JFlash/

Usage: gdb-py --batch -x JFlash.py -ex "py program_from_shell(<bin_file>)"

Copyright (c) 2016 Vitaly Kravtsov (in4lio@gmail.com)
See the LICENSE file.
"""

APP             = 'JFlash'
VERSION         = '0.4b3'

#  J-Link GDB Server
HOST            = 'localhost'
PORT            = 2331

LOADER          = 'LOADER/LOADER.bin'
DUMP            = 'dump.bin'

#  LOADER layout (according to MAP file)
#  NOTE: Use 'mapper.py' if you need to update the following definitions.
LD_COMPILER     = 1
LD_START        = 0x20000b3c
LD_STACK        = 0x20008000
LD_IFACE        = 0x2000248c
LD_IFACE_SZ     = 0x4010
LD_RTT          = 0x20002414

LD_FIELD_SZ     = 4
LD_DATA         = LD_IFACE
LD_DATA_SZ      = LD_IFACE_SZ - LD_FIELD_SZ * 4
LD_ADDR         = LD_DATA  + LD_DATA_SZ
LD_LEN          = LD_ADDR  + LD_FIELD_SZ
LD_STATE        = LD_LEN   + LD_FIELD_SZ
LD_ERR          = LD_STATE + LD_FIELD_SZ

#  LOADER running command or state
START           = 0
ERASE           = 1
WRITE_BLOCK     = 2
IDLE            = 0xFFFFFFFF

#  LOADER error
ERR_NONE        = 0
ERR_ADDR        = 1
ERR_ADDR_ALIGN  = 2
ERR_ADDR_END    = 3

#  MCU memory layout
RAM_START       = 0x20000000
EEPROM_START    = 0x08000000

import gdb
import sys
import os
import logging
from time import sleep
import filecmp

#  Logging
LOG             = APP + '.log'
LOG_LEVEL       = logging.DEBUG
LOG_FORMAT      = '%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s'
LOG_TIME        = '%H:%M:%S'

log = logging.getLogger( 'log' )
log.setLevel( LOG_LEVEL )

#  MCU registers
#  R0 .. R15
for x in range( 0, 16 ):
    #  I know, it's a really bad idea, but...
    globals()[ 'R%d' % x ] = 'r%d' % x

PC  = 'pc'
LR  = 'lr'
MSP = 'MSP'
PSP = 'PSP'

#  Execute GDB command
def execute( st ):
    return gdb.execute( st, to_string=True )

#  Execute GDB 'monitor' command
def monitor( st ):
    return execute( 'monitor ' + st )

#  Read DWORD for memory
def mem32( addr ):
    return long( execute( 'x ' + str( addr )).split( ':' )[ 1 ].strip(), 16 )

#  Write DWORD to memory
def set_mem32( addr, val ):
    return execute( 'set {int}%d = %d' % ( addr, val ))

#  Read register
def reg( r ):
    return long( gdb.parse_and_eval( '$' + r ))

#  Write register
def set_reg( r, val ):
    return execute( 'set $%s = %d' % ( r, val ))

#  Upload binary data form file to memory
def load_binary( fn, offset, start=None, end=None ):
    st = 'restore %s binary %d' % ( fn, offset )
    if start is not None:
        st += ' %d' % start
        if end is not None:
            st += ' %d' % end
    return execute( st )

#  Save data form memory to dump file
def dump_binary( fn, offset, l ):
    return execute( 'dump binary memory %s %d %d' % ( fn, offset, offset + l ))

#  Directory of script
SCRIPT_DIR = os.path.dirname( os.path.realpath( __file__ ))

#  Verify EEPROM
def verify( binary, binary_sz ):
    dump = os.path.join( SCRIPT_DIR, DUMP )
    dump_binary( dump, EEPROM_START, binary_sz )

    return filecmp.cmp( binary, dump )


#  MAIN SCRIPT

def program( binary ):
    log.info( 'MCU MDR32F9Qx %s %s', APP, VERSION )

    if not os.path.exists( binary ):
        log.error( 'Binary file not found (%s).', binary )
        return False

    binary_sz = os.path.getsize( binary )

    log.info( 'Binary file: %s', binary )
    log.info( 'Size: %d', binary_sz )
    log.info( 'MCU data buffer at %#X', LD_DATA )

    log.info( 'Hello!' )

    fb = monitor( 'reset 0' )
    log.debug( fb.strip())
    monitor( 'halt' )

    #  VERIFY EEPROM BEFORE PROGRAMMING

    if verify( binary, binary_sz ):
        log.info( 'Binary file exactly matches with EEPROM content.' )

        monitor( 'go' )
        return True

    #  START LOADER

    log.info( 'LOADER uploading...' )
    fb = load_binary( os.path.join( SCRIPT_DIR, LOADER ), RAM_START )
    log.debug( fb.strip())
    set_reg( MSP, LD_STACK )
    set_reg( PC , LD_START & ~1 )
    set_mem32( 0xE000E008, 0x20000000 )
    set_mem32( LD_STATE, START )
    monitor( 'exec SetRTTAddr ' + hex( LD_RTT ))
    monitor( 'go' )

    #  Check LOADER is started
    sleep( 0.1 )
    if mem32( LD_STATE ) != IDLE:
        log.error( 'LOADER is not running.' )
        return False

    #  ERASE EEPROM

    log.info( 'EEPROM erasing...' )
    monitor( 'halt' )
    set_mem32( LD_STATE, ERASE )
    monitor( 'go' )

    #  Wait for ending
    while mem32( LD_STATE ) == ERASE:
        log.info( 'TICK' )
        sleep( 0.2 )

    sleep( 0.2 )
    monitor( 'halt' )

    #  Check very first DWORD
    if mem32( EEPROM_START ) != 0xFFFFFFFF:
        log.error( 'EEPROM is not empty.' )
        return False

    #  WRITING CYCLE

    rest = binary_sz
    block = 0
    start = 0
    while ( rest ):
        if rest > LD_DATA_SZ:
            sz = LD_DATA_SZ
            rest -= LD_DATA_SZ
        else:
            sz = rest
            rest = 0

        block += 1
        log.info( 'BLOCK %d writing...', block )
        fb = load_binary( binary, LD_DATA - start, start, start + sz )
        log.debug( fb.strip())

        set_mem32( LD_ADDR, EEPROM_START + start )
        set_mem32( LD_LEN, ( sz + 3 ) // 4 )  # size in DWORDs
        set_mem32( LD_STATE, WRITE_BLOCK )

        monitor( 'go' )

        sleep( 0.1 )
        #  Wait for ending
        while mem32( LD_STATE ) == WRITE_BLOCK:
            sleep( 0.2 )

        monitor( 'halt' )

        #  Check error
        if mem32( LD_ERR ) != ERR_NONE:
            log.error( 'Fail to write data (E%d).', mem32( LD_ERR ))
            return False

        start += sz

    #  VERIFY EEPROM AFTER PROGRAMMING

    if not verify( binary, binary_sz ):
        log.error( 'Binary file does NOT match with EEPROM content.' )
        return False

    log.info( '**** SUCCESS! ****' )

    monitor( 'go' )
    fb = monitor( 'reset 0' )
    log.info( fb.strip())

    return True


#  Wrapper for program EEPROM from Eclipce
def program_from_eclipse( binary ):
    #  Write log to file
    h = logging.FileHandler( LOG, mode = 'w' )
    h.setFormatter( logging.Formatter( LOG_FORMAT, LOG_TIME ))
    log.addHandler( h )
    try:
        result = program( binary )

    except Exception as e:
        log.exception( str( e ))
        result = False

    log.removeHandler( h )
    return result

#  Wrapper for program EEPROM from shell
def program_from_shell( binary ):
    #  Write log to stdout
    h = logging.StreamHandler( sys.stdout )
    h.setFormatter( logging.Formatter( LOG_FORMAT, LOG_TIME ))
    log.addHandler( h )

    execute( 'set pagination off' )
    log.info( 'J-Link GDB Server connecting...' )
    try:
        execute( 'target remote %s:%d' % ( HOST, PORT ))

    except Exception as e:
        log.error( 'Fail to connect.' )
        log.info( e.message )
        log.info( 'Please start J-Link GDB Server first.' )
        log.removeHandler( h )
        return False

    result = program( binary )

    log.removeHandler( h )
    return result


#  GDB "load" command
class LoadCommand( gdb.Command ):
    def __init__( self ):
        #  Redefine "load" command
        super( type( self ), self ).__init__( 'load', gdb.COMMAND_FILES )

    def invoke( self, arg, from_tty ):
        if not program_from_eclipse( arg ):
            #  Cancel debugging
            execute( 'quit' )

LoadCommand()
