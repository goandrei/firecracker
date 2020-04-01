# Copyright 2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Performance benchmark for memory emulation."""

import os
import json
import re
import pytest
from subprocess import PIPE, run

import host_tools.network as net_tools

C_NAME = 'stream.c'

BINARY_NAME = 'stream_mpi'

OMP_LIB = 'libgomp.so.1'

LIB_PATH = '/usr/lib/x86_64-linux-gnu/{}'.format(OMP_LIB)

COMPILE_CMD = 'gcc -ffreestanding -fopenmp -mcmodel=medium -O3 -march=znver1 -DSTREAM_ARRAY_SIZE={} -DNTIMES={} -DOFFSET={} {} -o {}'

BYTE_IN_KBYTE = 8 / 1024

NTIMES = 100

OFFSET = 512

MEM_OFFSET = 1024 #MB

REMOTE_ROOT_PATH = '/root'

NUM_THREADS = 'Number of Threads requested = {}'

BENCH_KERNELS = [
    ('Copy', -8),
    ('Scale', -7),
    ('Add', -6),
    ('Triad', -5)
]

RESULT_VALUES = [
    ('Best Rate MB/s', 1), 
    ('Avg time', 2),
    ('Min time', 3),
    ('Max time', 4)
]

PERFORMANCE_METRICS = ('Triad', 'Best Rate MB/s')

TARGET = 0.95


def get_l3_cache_size():
    """Gets the L3 level cache size(in KBs) available on the machine."""
    proc = run('lscpu | grep "L3 cache"', stdout=PIPE, stderr=PIPE, shell=True, 
            universal_newlines=True)
    assert proc.stderr == ''

    l3_cache = re.findall(r'\d+K', proc.stdout)[0]
    return int(l3_cache.replace('K', ''))


L3_CACHE = get_l3_cache_size()


def parse_output(output, ret_json=False):
    """Parse the output of the benchmark."""
    print(output)
    output_lines = output.split('\n')
    assert len(output_lines) > 1
    assert 'Solution Validates' in output_lines[-3]

    results = dict()
    for (kernel, index) in BENCH_KERNELS:
        line = output_lines[index]
        line = re.sub(' +', ' ', line)
        line_values = line.split(' ')
        results[kernel] = dict()
        for result_value, result_index in RESULT_VALUES:
            results[kernel][result_value] = line_values[result_index]

    if ret_json == True:
        return json.dumps(results)

    return float(results[PERFORMANCE_METRICS[0]][PERFORMANCE_METRICS[1]])


def compile_stream(local_test_path, stream_array_size):
    """Compiles STREAM benchmark with given parameters."""
    c_path = os.path.join(local_test_path, C_NAME)
    binary_path = os.path.join(local_test_path, BINARY_NAME)
    cmd = COMPILE_CMD.format(stream_array_size, NTIMES, OFFSET,
                             c_path, binary_path)
    proc = run(cmd, shell=True, check=True) 


def prepare_binary(local_test_path, vcpus=1, l3_cache = 8192):
    """Prepare the benchmark's binary."""

    # Must be 4 times bigger than the total L3 cache memory 
    # In order to fill the cache and test the main memory too.
    stream_array_size = int((4 * vcpus * l3_cache) / BYTE_IN_KBYTE)
    stream_array_size = 5120000 * vcpus
    compile_stream(local_test_path, stream_array_size)


def get_local_test_path():
    """Returns the directory of the running test."""
    file_name = os.path.basename(__file__)
    
    # This variable has the following format : path/to/test::test_name [image]
    local_test_path = os.getenv('PYTEST_CURRENT_TEST') \
        .split('::')[0] \
        .replace(file_name, '')

    return local_test_path


def copy_benchmark_on_guest(ssh_connection, local_bench_path):
    """Copy benchmark binary and OpenMP library on the guest."""
    ssh_connection.scp_file(local_path=local_bench_path,
                            remote_path=REMOTE_ROOT_PATH)
    ssh_connection.scp_file(local_path=LIB_PATH, 
                            remote_path=LIB_PATH)


def run_test_host(local_binary_path, vcpus):
    """Run the benchmark on host."""
    os.environ['OMP_NUM_THREADS'] = str(vcpus)
    os.environ['OMP_PROC_BIND'] = 'SPREAD'
    os.environ['KMP_AFFINITY'] = 'compact'
    proc = run('./{}'.format(local_binary_path), stdout=PIPE, stderr=PIPE,
                              universal_newlines=True)
    assert proc.stderr == ''
    assert NUM_THREADS.format(vcpus) in proc.stdout

    return proc.stdout


def run_test_guest(ssh_connection, local_binary_path, vcpus):
    """Run the benchmark on guest."""
    copy_benchmark_on_guest(ssh_connection, local_binary_path)
    
    cmd = """chmod +x {binary} && 
             export OMP_PROC_BIND=SPREAD &&
             export KMP_AFFINITY=compact &&
             export OMP_NUM_THREADS={threads} &&
             ./{binary}""".format(binary=BINARY_NAME, threads=vcpus)
    
    _, stdout, stderr = ssh_connection.execute_command(cmd)
    assert stderr.read().decode('utf-8') == ''

    stdout = stdout.read().decode('utf-8')
    assert NUM_THREADS.format(vcpus) in stdout 

    return stdout


def test_memory_performance_1_vcpu(test_microvm_with_ssh, network_config):
    """Execute memory emulation tests with 1 vCPU."""
    vcpus = 1
    microvm = test_microvm_with_ssh
    microvm.spawn()
    
    microvm.basic_config(mem_size_mib=1024, vcpu_count=vcpus)

    _tap, _, _ = microvm.ssh_network_config(network_config, '1')

    microvm.start()
    ssh_connection = net_tools.SSHConnection(microvm.ssh_config)

    local_test_path = get_local_test_path()
    local_binary_path = os.path.join(local_test_path, BINARY_NAME)
    prepare_binary(local_test_path, vcpus)

    host_stdout = run_test_host(local_binary_path, vcpus)
    guest_stdout = run_test_guest(ssh_connection, local_binary_path, vcpus)


    guest = parse_output(guest_stdout)
    host = parse_output(host_stdout)
    print('guest {} host {}'.format(guest, host));
    performance = guest / host
    print('vCPUs : {} | Pinning : false | Performance : {}'.format(vcpus, performance))
    assert performance >= TARGET
    assert 1 == 0

@pytest.mark.skip(reason="no way of currently testing this")
def test_memory_performance_4_vcpu(test_microvm_with_ssh, network_config):
    """Execute memory emulation tests with 4 vCPUs."""
    vcpus = 4
    microvm = test_microvm_with_ssh
    microvm.spawn()

    microvm.basic_config(mem_size_mib=1024, vcpu_count=vcpus)

    _tap, _, _ = microvm.ssh_network_config(network_config, '1')

    microvm.start()
    ssh_connection = net_tools.SSHConnection(microvm.ssh_config)

    local_test_path = get_local_test_path()
    local_binary_path = os.path.join(local_test_path, BINARY_NAME)
    prepare_binary(local_test_path, vcpus)

    host_stdout = run_test_host(local_binary_path, vcpus)
    guest_stdout = run_test_guest(ssh_connection, local_binary_path, vcpus)

    performance = parse_output(guest_stdout) / parse_output(host_stdout)
    print(performance)
    print('vCPUs : {} | Pinning : false | Performance : {}'.format(vcpus, performance))
    assert performance >= 2

@pytest.mark.skip(reason="no way of currently testing this")
def test_memory_performance_16_vcpu(test_microvm_with_ssh, network_config):
    """Execute memory emulation tests with 16 vCPUs."""
    vcpus = 16
    microvm = test_microvm_with_ssh
    microvm.spawn()
    
    microvm.basic_config(mem_size_mib=2048, vcpu_count=vcpus)

    _tap, _, _ = microvm.ssh_network_config(network_config, '1')

    microvm.start()
    ssh_connection = net_tools.SSHConnection(microvm.ssh_config)

    local_test_path = get_local_test_path()
    local_binary_path = os.path.join(local_test_path, BINARY_NAME)
    prepare_binary(local_test_path, vcpus)

    host_stdout = run_test_host(local_binary_path, vcpus)
    guest_stdout = run_test_guest(ssh_connection, local_binary_path, vcpus)

    performance = parse_output(guest_stdout) / parse_output(host_stdout)
    print('vCPUs : {} | Pinning : false | Performance : {}'.format(vcpus, performance))
    assert performance >= TARGET
@pytest.mark.skip(reason="no way of currently testing this")
def test_memory_performance_32_vcpu(test_microvm_with_ssh, network_config):
    """Execute memory emulation tests with 32 vCPUs."""
    vcpus = 32
    microvm = test_microvm_with_ssh
    microvm.spawn()

    microvm.basic_config(mem_size_mib=4096, vcpu_count=vcpus)

    _tap, _, _ = microvm.ssh_network_config(network_config, '1')

    microvm.start()
    ssh_connection = net_tools.SSHConnection(microvm.ssh_config)

    local_test_path = get_local_test_path()
    local_binary_path = os.path.join(local_test_path, BINARY_NAME)
    prepare_binary(local_test_path, vcpus)

    host_stdout = run_test_host(local_binary_path, vcpus)
    guest_stdout = run_test_guest(ssh_connection, local_binary_path, vcpus)

    performance = parse_output(guest_stdout) / parse_output(host_stdout)
    print('vCPUs : {} | Pinning : false | Performance : {}'.format(vcpus, performance))
    assert performance >= TARGET
