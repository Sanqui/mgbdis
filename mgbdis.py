#!/usr/local/bin/python3

"""Disassemble a Game Boy ROM into RGBDS compatible assembly code"""

__author__ = 'Matt Currie'
__version__ = '1.0.1'
__copyright__ = 'Copyright 2018 by Matt Currie'
__license__ = 'MIT'

import argparse
import glob
import hashlib
import os
from shutil import copyfile

from instruction_set import instructions, cb_instructions

default_symbols = [
    '00:0000 RST_00',
    '00:0000 .code:8',
    '00:0008 RST_08',
    '00:0008 .code:8',
    '00:0010 RST_10',
    '00:0010 .code:8',
    '00:0018 RST_18',
    '00:0018 .code:8',
    '00:0020 RST_20',
    '00:0020 .code:8',
    '00:0028 RST_28',
    '00:0028 .code:8',
    '00:0030 RST_30',
    '00:0030 .code:8',
    '00:0038 RST_38',
    '00:0038 .code:8',

    '00:0040 VBlankInterrupt',
    '00:0040 .code:8',
    '00:0048 LCDCInterrupt',
    '00:0048 .code:8',
    '00:0050 TimerOverflowInterrupt',
    '00:0050 .code:8',
    '00:0058 SerialTransferCompleteInterrupt',
    '00:0058 .code:8',
    '00:0060 JoypadTransitionInterrupt',
    '00:0060 .code:8',

    '00:0100 Boot',
    '00:0104 HeaderLogo',
    '00:0104 .data:30',
    '00:0134 HeaderTitle',
    '00:0134 .text:10',
    '00:0144 .data:c',
    '00:0144 HeaderNewLicenseeCode',
    '00:0146 HeaderSGBFlag',
    '00:0147 HeaderCartridgeType',
    '00:0148 HeaderROMSize',
    '00:0149 HeaderRAMSize',
    '00:014a HeaderDestinationCode',
    '00:014b HeaderOldLicenseeCode',
    '00:014c HeaderMaskROMVersion',
    '00:014d HeaderComplementCheck',
    '00:014e HeaderGlobalChecksum',
]

gbc_symbols = [
    '00:0134 .text:b',
    '00:013f HeaderManufacturerCode',
    '00:013f .text:4',
    '00:0143 HeaderCGBFlag',
    '00:0143 .data:1'
]

hardware_labels = {
    0xFF00: 'rP1',
    0xFF01: 'rSB',
    0xFF02: 'rSC',
    0xFF04: 'rDIV',
    0xFF05: 'rTIMA',
    0xFF06: 'rTMA',
    0xFF07: 'rTAC',
    0xFF0F: 'rIF',
    0xFF40: 'rLCDC',
    0xFF41: 'rSTAT',
    0xFF42: 'rSCY',
    0xFF43: 'rSCX',
    0xFF44: 'rLY',
    0xFF45: 'rLYC',
    0xFF46: 'rDMA',
    0xFF47: 'rBGP',
    0xFF48: 'rOBP0',
    0xFF49: 'rOBP1',
    0xFF4A: 'rWY',
    0xFF4B: 'rWX',
    0xFF4D: 'rKEY1',
    0xFF4F: 'rVBK',
    0xFF51: 'rHDMA1',
    0xFF52: 'rHDMA2',
    0xFF53: 'rHDMA3',
    0xFF54: 'rHDMA4',
    0xFF55: 'rHDMA5',
    0xFF56: 'rRP',
    0xFF68: 'rBCPS',
    0xFF69: 'rBCPD',
    0xFF6A: 'rOCPS',
    0xFF6B: 'rOCPD',
    0xFF70: 'rSVBK',
    0xFFFF: 'rIE',
    0xFF24: 'rNR50',
    0xFF25: 'rNR51',
    0xFF26: 'rNR52',
    0xFF10: 'rNR10',
    0xFF11: 'rNR11',
    0xFF12: 'rNR12',
    0xFF13: 'rNR13',
    0xFF14: 'rNR14',
    0xFF16: 'rNR21',
    0xFF17: 'rNR22',
    0xFF18: 'rNR23',
    0xFF19: 'rNR24',
    0xFF1A: 'rNR30',
    0xFF1B: 'rNR31',
    0xFF1C: 'rNR32',
    0xFF1D: 'rNR33',
    0xFF1E: 'rNR34',
    0xFF20: 'rNR41',
    0xFF21: 'rNR42',
    0xFF22: 'rNR43',
    0xFF23: 'rNR44',
}

def abort(message):
    print(message)
    os._exit(1)


def hex_word(value):
    return '${:04x}'.format(value)


def hex_byte(value):
    return '${:02x}'.format(value)


def bytes_to_string(data):
    return ' '.join(hex_byte(byte) for byte in data)


def rom_address_to_mem_address(address):
    if address < 0x4000:
        return address
    else:
        return ((address % 0x4000) + 0x4000)


def to_signed(value):
    if value > 127:
        return (256 - value) * -1
    return value


class Bank:

    def __init__(self, number):
        self.bank_number = number
        self.blocks = dict()
        self.disassembled_addresses = set()
        self.labelled_addresses = dict()

        if number == 0:
            self.memory_base_address = 0
            self.rom_base_address = 0
        else:
            self.memory_base_address = 0x4000            
            self.rom_base_address = (number - 1) * 0x4000

        self.target_addresses = dict({
            'call': set(),
            'jp': set(),
            'jr': set()
        })

        self.instruction_label_prefixes = dict({
            'call': 'Call',
            'jp': 'Jump',
            'jr': 'jr'
        })

        # each bank defaults to having a single code block
        self.add_block(self.memory_base_address, 'code', 0x4000)

        self.disassemble_block_range = dict({
            'code': self.process_code_in_range,
            'data': self.process_data_in_range,
            'text': self.process_text_in_range    
        })


    def add_label(self, instruction_name, address):
        if address not in self.target_addresses[instruction_name]:
            self.target_addresses[instruction_name].add(address)


    def add_block(self, address, block_type, length):
        if address >= self.memory_base_address:
            self.blocks[address] = {
                'type': block_type, 
                'length': length
            }


    def resolve_blocks(self):
        block_start_addresses = sorted(self.blocks.keys())
        resolved_blocks = dict()

        for index in range(len(block_start_addresses)):

            start_address = block_start_addresses[index]
            block = self.blocks[start_address]
            end_address = start_address + block['length']

            # check if there is another block after this block
            next_start_address = None
            if index < len(block_start_addresses) - 1:
                next_start_address = block_start_addresses[index + 1]
                
                # if the next block starts before this one finishes, then adjust end address
                if next_start_address < end_address:
                    end_address = next_start_address

            resolved_blocks[start_address] = {
                'type': block['type'],
                'length': end_address - start_address
            }

            if next_start_address is None and (end_address != self.memory_base_address + 0x4000):
                # no more blocks and didn't finish at the end of the block, so finish up with a code block
                resolved_blocks[end_address] = {
                    'type': 'code',
                    'length': (self.memory_base_address + 0x4000) - end_address
                }

            if next_start_address is not None and end_address < next_start_address:
                # we have another block, but there is a gap until the next block, so fill in the gap with a code block
                resolved_blocks[end_address] = {
                    'type': 'code',
                    'length': next_start_address - end_address
                }

        self.blocks = resolved_blocks


    def get_label_for_instruction_operand(self, instruction_name, address):
        if address not in self.disassembled_addresses:
            return None

        if address in self.labelled_addresses:
            # if the address has a specific label then just use that
            return self.labelled_addresses[address]

        if address in self.target_addresses[instruction_name]:
            return self.format_label(instruction_name, address)

        return None


    def get_labels_for_non_code_address(self, address):
        labels = list()

        if address in self.labelled_addresses:
            if self.labelled_addresses[address][0] == '.':
                labels.append(self.labelled_addresses[address] + ':')
            else:
                labels.append(self.labelled_addresses[address] + '::')

        return labels


    def get_labels_for_address(self, address):
        labels = list()

        if address in self.labelled_addresses:
            # if the address has a specific label then just use that
            if self.labelled_addresses[address][0] == '.':
                labels.append(self.labelled_addresses[address] + ':')
            else:
                labels.append(self.labelled_addresses[address] + '::')
        else:
            # otherwise check generated ones
            for instruction_name in ['call', 'jp', 'jr']:
                if address in self.target_addresses[instruction_name]:
                    labels.append(self.format_label(instruction_name, address) + ':')

        return labels


    def format_label(self, instruction_name, address):
        return '{0}_{1:03x}_{2:04x}'.format(self.instruction_label_prefixes[instruction_name], self.bank_number, address)


    def format_instruction(self, instruction_name, operands, address = None, source_bytes = None):
        instruction = '    {instruction_name} {operands}'.format(
            instruction_name=instruction_name, 
            operands=', '.join(operands)
        )

        if False: #address is not None and source_bytes is not None:
            return '{0:<50}; {1}: {2}'.format(instruction, hex_word(address), bytes_to_string(source_bytes))
        else:
            return '{}'.format(instruction)


    def format_data(self, data):
        return self.format_instruction('DB', data)


    def append_output(self, text):
        self.output.append(text)


    def append_labels_to_output(self, labels):
        self.append_empty_line_if_none_already()
        self.append_output('\n'.join(labels))


    def append_empty_line_if_none_already(self):
        if len(self.output) > 0 and self.output[len(self.output) - 1] != '':
            self.append_output('')


    def disassemble(self, rom, first_pass = False):
        self.first_pass = first_pass

        if first_pass:
            self.resolve_blocks()

        self.output = list()

        if self.bank_number == 0:
            self.append_output('SECTION "ROM Bank ${0:03x}", ROM0[$0]'.format(self.bank_number))
        else:
            self.append_output('SECTION "ROM Bank ${0:03x}", ROMX[$4000], BANK[${0:x}]'.format(self.bank_number))
        self.append_output('')

        block_start_addresses = sorted(self.blocks.keys())

        for index in range(len(block_start_addresses)):
            start_address = block_start_addresses[index]
            block = self.blocks[start_address]
            end_address = start_address + block['length']
            self.disassemble_block_range[block['type']](rom, self.rom_base_address + start_address, self.rom_base_address + end_address)
            self.append_empty_line_if_none_already()

        return '\n'.join(self.output)


    def process_code_in_range(self, rom, start_address, end_address):
        if not self.first_pass and debug:
            print('Disassembling code in range: {} - {}'.format(hex_word(start_address), hex_word(end_address)))

        self.pc = start_address
        while self.pc < end_address:
            instruction = self.disassemble_at_pc(rom, end_address)


    def disassemble_at_pc(self, rom, end_address):
        pc = self.pc
        pc_mem_address = rom_address_to_mem_address(pc)
        length = 1
        opcode = rom.data[pc]
        comment = None
        operands = None
        operand_values = list()

        if opcode not in instructions:
            abort('Unhandled opcode: {} at {}'.format(hex_byte(opcode), hex_word(pc)))

        if opcode == 0xCB:
            cb_opcode = rom.data[pc + 1]
            length += 1

            instruction_name = rom.cb_instruction_name[cb_opcode]
            operands = rom.cb_instruction_operands[cb_opcode]
        else:
            instruction_name = rom.instruction_names[opcode]
            operands = rom.instruction_operands[opcode]


        if instruction_name == 'stop' or instruction_name == 'halt':
            if rom.data[pc + 1] == 0x00:
                # rgbds adds a nop instruction after a stop/halt, so if that instruction 
                # exists then we can insert it as a stop/halt command with length 2
                length += 1
            else:
                # otherwise handle it as a data byte
                instruction_name = 'DB'
                operands = [hex_byte(opcode)]


        # figure out the operand values for each operand
        for operand in operands:
            value = None

            if operand == 'a16':
                length += 2
                value = rom.data[pc + 1] + rom.data[pc + 2] * 256
                operand_values.append(hex_word(value))
            
            elif operand == '[a16]':
                length += 2
                value = rom.data[pc + 1] + rom.data[pc + 2] * 256
                operand_values.append('[' + hex_word(value) + ']')

                # rgbds converts "ld [$ff40],a" into "ld [$ff00+40],a" automatically,
                # so use a macro to encode it as data to ensure exact binary reproduction of the rom
                if value >= 0xff00 and (opcode == 0xea or opcode == 0xfa):
                    rom.has_ld_long = True

                    # use ld_long macro
                    instruction_name = 'ld_long'

                    # cannot wrap the address value with square brackets
                    operand_values.pop()
                    operand_values.append(hex_word(value))

            elif operand == '[$ff00+a8]':
                length += 1
                value = rom.data[pc + 1]
                full_value = 0xff00 + value

                if full_value in hardware_labels:
                    operand_values.append('[{}]'.format(hardware_labels[full_value]))
                else:
                    operand_values.append('[$ff00+' + hex_byte(value) + ']')

            elif operand == 'd8':
                length += 1
                value = rom.data[pc + 1]
                operand_values.append(hex_byte(value))

            elif operand == 'd16':
                length += 2
                value = rom.data[pc + 1] + rom.data[pc + 2] * 256
                operand_values.append(hex_word(value))

            elif operand == 'r8':
                length += 1
                value = to_signed(rom.data[pc + 1])
                if value < 0:
                    operand_values.append('-' + hex_byte(abs(value)))
                else:
                    operand_values.append(hex_byte(value))
                
            elif operand == 'pc+r8':
                length += 1
                value = to_signed(rom.data[pc + 1])

                # calculate the absolute address for the jump
                value = pc + 2 + value

                relative_value = value - pc
                if relative_value >= 0:
                    operand_values.append('@+' + hex_byte(relative_value))
                else:
                    operand_values.append('@-' + hex_byte(relative_value * -1))

                target_bank = value // 0x4000

                # convert to banked value so it can be used as a label
                value = rom_address_to_mem_address(value)

                if self.bank_number != target_bank:
                    # don't use labels for relative jumps across banks
                    value = None

                if target_bank < self.bank_number:
                    # output as data, otherwise RGBDS will complain
                    instruction_name = 'DB'
                    operand_values = [hex_byte(opcode), hex_byte(rom.data[pc + 1])]

                    # exit the loop to avoid processing the operands any further
                    break

            elif operand == 'sp+r8':
                length += 1
                value = to_signed(rom.data[pc + 1])
                
                if value < 0:
                    operand_values.append('sp-' + hex_byte(abs(value)))
                else:
                    operand_values.append('sp+' + hex_byte(value))

            elif type(operand) is str:
                operand_values.append(operand)

            else:
                operand_values.append(hex_byte(operand))
            

            if instruction_name in ['jr', 'jp', 'call'] and value is not None and value < 0x8000:
                mem_address = rom_address_to_mem_address(value)

                # dont allow switched banks to create labels in bank 0
                if (mem_address < 0x4000 and self.bank_number == 0) or (mem_address >= 0x4000 and self.bank_number > 0):

                    if self.first_pass:
                        # add the label
                        self.add_label(instruction_name, mem_address)
                    else:
                        # fetch the label name
                        label = self.get_label_for_instruction_operand(instruction_name, mem_address)
                        if label is not None:
                            # remove the address from operand values and use the label instead
                            operand_values.pop()
                            operand_values.append(label)
            elif value is not None and value >= 0xc000:
                if value in self.labelled_addresses:
                    label = self.labelled_addresses[value]
                    operand = operand_values.pop()
                    if operand.startswith('['):
                        new_operand = f"[{label}]"
                    else:
                        new_operand = label
                    operand_values.append(new_operand)
                            

        # check the instruction is not spanning 2 banks
        if pc + length - 1 >= end_address:
            # must handle it as data
            length = 1
            instruction_name = 'DB'
            operand_values = [hex_byte(opcode)]

        self.pc += length

        if self.first_pass:
            self.disassembled_addresses.add(pc_mem_address)
        else:
            labels = self.get_labels_for_address(pc_mem_address)
            if len(labels):
                self.append_labels_to_output(labels)

            if comment is not None:
                self.append_output(comment)

            instruction_bytes = rom.data[pc:pc + length]
            self.append_output(self.format_instruction(instruction_name, operand_values, pc_mem_address, instruction_bytes))

            # add some empty lines after returns and jumps to break up the code blocks
            if instruction_name in ['ret', 'reti', 'jr', 'jp']:
                if (
                    instruction_name == 'jr' or
                    (instruction_name == 'jp' and len(operand_values) > 1) or
                    (instruction_name == 'ret' and len(operand_values) > 0)
                ):
                    # conditional or jr
                    self.append_output('')
                else:
                    # always executes
                    self.append_output('')
                    self.append_output('')


    def process_data_in_range(self, rom, start_address, end_address):
        if not self.first_pass and debug:
            print('Outputting data in range: {} - {}'.format(hex_word(start_address), hex_word(end_address)))

        values = list()

        for address in range(start_address, end_address):
            mem_address = rom_address_to_mem_address(address)

            labels = self.get_labels_for_non_code_address(mem_address)
            if len(labels):
                # add any existing values to the output and reset the list
                if len(values) > 0:
                    self.append_output(self.format_data(values))
                    values = list()

                self.append_labels_to_output(labels)

            values.append(hex_byte(rom.data[address]))

            # output max of 16 bytes per line, and ensure any remaining values are output
            if len(values) == 16 or (address == end_address - 1 and len(values)):
                self.append_output(self.format_data(values))
                values = list()


    def process_text_in_range(self, rom, start_address, end_address):
        if not self.first_pass and debug:
            print('Outputting text in range: {} - {}'.format(hex_word(start_address), hex_word(end_address)))

        values = list()
        text = ''

        for address in range(start_address, end_address):
            mem_address = rom_address_to_mem_address(address)

            labels = self.get_labels_for_non_code_address(mem_address)
            if len(labels):
                # add any existing values to the output and reset the list
                if len(text):
                    values.append('"{}"'.format(text))
                    text = ''

                if len(values):
                    self.append_output(self.format_data(values))
                    values = list()

                self.append_labels_to_output(labels)

            byte = rom.data[address]
            if byte >= 0x20 and byte < 0x7F:
                text += chr(byte)
            else:
                if len(text):
                    values.append('"{}"'.format(text))
                    text = ''
                values.append(hex_byte(byte))

        if len(text):
            values.append('"{}"'.format(text))

        if len(values):
            self.append_output(self.format_data(values))



class ROM:

    def __init__(self, rom_path):
        self.script_dir = os.path.dirname(os.path.realpath(__file__))
        self.rom_path = rom_path
        self.load()
        self.split_instructions()
        self.has_ld_long = False

        print('ROM MD5 hash:', hashlib.md5(self.data).hexdigest())

        # add some bytes to avoid an index out of range error
        # when processing last few instructions in the rom
        self.data += b'\x00\x00'

        self.banks = dict()
        for bank in range(0, self.num_banks):
            self.banks[bank] = Bank(bank)

        self.init_symbols()


    def load(self):
        if os.path.isfile(self.rom_path):
            print('Loading "{}"...'.format(self.rom_path))
            self.data = open(self.rom_path, 'rb').read()  
            self.rom_size = len(self.data)
            self.num_banks = self.rom_size // 0x4000
        else:
            abort('"{}" not found'.format(self.rom_path))


    def split_instructions(self):
        # split the instructions and operands
        self.instruction_names = dict()
        self.instruction_operands = dict()
        self.cb_instruction_name = dict()
        self.cb_instruction_operands = dict()

        for opcode in instructions:
            instruction_parts = instructions[opcode].split()
            self.instruction_names[opcode] = instruction_parts[0]
            if len(instruction_parts) > 1:
                self.instruction_operands[opcode] = instruction_parts[1].split(',')
            else:
                self.instruction_operands[opcode] = list()

        for cb_opcode in cb_instructions:
            instruction_parts = cb_instructions[cb_opcode].split()
            self.cb_instruction_name[cb_opcode] = instruction_parts[0]
            if len(instruction_parts) > 1:
                self.cb_instruction_operands[cb_opcode] = instruction_parts[1].split(',')
            else:
                self.cb_instruction_operands[cb_opcode] = list()


    def init_symbols(self):
        for symbol_def in default_symbols:
            self.add_symbol_definition(symbol_def)

        if self.supports_gbc():
            for symbol_def in gbc_symbols:
                self.add_symbol_definition(symbol_def)

        self.load_sym_file()


    def add_symbol_definition(self, symbol_def):
        try:
            location, label = symbol_def.split()
            bank, address = location.split(':')
            bank = int(bank, 16)
            address = int(address, 16)
        except:
            print("Ignored invalid symbol definition: {}\n".format(symbol_def))
        else:
            label_parts = label.split(':')

            if label[0] == '.' and len(label_parts) == 2:
                block_type = label_parts[0].lower()
                data_length = int(label_parts[1], 16)

                if block_type in ['.byt', '.data']:
                    self.banks[bank].add_block(address, 'data', data_length)

                elif block_type in ['.asc', '.text']:
                    self.banks[bank].add_block(address, 'text', data_length)

                elif block_type in ['.asc', '.code']:
                    self.banks[bank].add_block(address, 'code', data_length)

            else:
                # add the label
                if address >= 0x8000: # RAM
                    # slow hack
                    for b in self.banks:
                        self.banks[b].labelled_addresses[address] = label
                else:
                    self.banks[bank].labelled_addresses[address] = label


    def supports_gbc(self):
        return ((self.data[0x143] & 0x80) == 0x80)


    def load_sym_file(self):
        filepath = os.path.splitext(self.rom_path)[0] + '.sym'

        if os.path.isfile(filepath):
            print('Processing symbol file "{}"...'.format(filepath))

            f = open(filepath, 'r')

            for line in f:
                # ignore comments and empty lines
                if line[0] != ';' and len(line.strip()):
                    self.add_symbol_definition(line)

            f.close()


    def disassemble(self, output_dir):

        self.output_directory = os.path.abspath(output_dir.rstrip(os.sep))

        if os.path.exists(self.output_directory):
            if not args.overwrite:
                abort('Output directory "{}" already exists!'.format(self.output_directory))

            if not os.path.isdir:
                abort('Output path "{}" already exists and is not a directory!'.format(self.output_directory))
        else:
            os.makedirs(self.output_directory)


        print('Generating labels...')
        self.generate_labels()

        print('Generating disassembly', end='')
        if debug:
            print('')

        for bank in range(0, self.num_banks):
            self.write_bank_asm(bank)

        self.copy_hardware_inc()
        self.write_game_asm()
        self.write_makefile()

        print('\nDisassembly generated in "{}"'.format(self.output_directory))

        
    def generate_labels(self):
        for bank in range(0, self.num_banks):
            self.banks[bank].disassemble(rom, True)


    def write_bank_asm(self, bank):
        if not debug:
            # progress indicator
            print('.', end='', flush=True)

        path = os.path.join(self.output_directory, 'bank_{0:03x}.asm'.format(bank))
        f = open(path, 'w')

        self.write_header(f)
        f.write(self.banks[bank].disassemble(rom))

        f.close()        


    def write_header(self, f):
        f.write('; Disassembly of "{}"\n'.format(os.path.basename(self.rom_path)))
        f.write('; This file was created with {}\n'.format(app_name))
        f.write('; https://github.com/mattcurrie/mgbdis\n\n')


    def copy_hardware_inc(self):
        src = os.path.join(self.script_dir, 'hardware.inc')
        dest = os.path.join(self.output_directory, 'hardware.inc')
        copyfile(src, dest)


    def write_game_asm(self):
        path = os.path.join(self.output_directory, 'game.asm')
        f = open(path, 'w')        

        self.write_header(f)

        if self.has_ld_long:

            f.write(
"""ld_long: MACRO
    IF STRLWR("\\1") == "a" 
        ; ld a, [$ff40]
        db $FA
        dw \\2
    ELSE 
        IF STRLWR("\\2") == "a" 
            ; ld [$ff40], a
            db $EA
            dw \\1
        ENDC
    ENDC
ENDM

""")

        f.write('INCLUDE "hardware.inc"')
        for bank in range(0, self.num_banks):
            f.write('\nINCLUDE "bank_{0:03x}.asm"'.format(bank))
        f.close()


    def write_makefile(self):
        rom_extension = 'gb'
        if self.supports_gbc():
            rom_extension = 'gbc'

        path = os.path.join(self.output_directory, 'Makefile')
        f = open(path, 'w')

        f.write('all: game.{}\n\n'.format(rom_extension))

        f.write('game.o: game.asm bank_*.asm\n')
        f.write('\trgbasm -o game.o game.asm\n\n')

        f.write('game.{}: game.o\n'.format(rom_extension))
        f.write('\trgblink -n game.sym -m $*.map -o $@ $<\n')
        f.write('\trgbfix -v -p 255 $@\n\n')

        f.write('clean:\n')
        f.write('\trm -f game.o game.{}\n'.format(rom_extension))

        f.close()



app_name = 'mgbdis v{version} - Game Boy ROM disassembler by {author}.'.format(version=__version__, author=__author__)
parser = argparse.ArgumentParser(description=app_name)
parser.add_argument('rom_path', help='Game Boy (Color) ROM file to disassemble')
parser.add_argument('--output-dir', default='disassembly', help='Directory to write the files into. Defaults to "disassembly"', action='store')
parser.add_argument('--overwrite', help='Allow generating a disassembly into an already existing directory', action='store_true')
parser.add_argument('--debug', help='Display debug output', action='store_true')
args = parser.parse_args()

debug = args.debug

rom = ROM(args.rom_path)
rom.disassemble(args.output_dir)
