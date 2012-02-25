#!/bin/env python

"""
The MIT License

Copyright (c) 2010 The Chicago Tribune & Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from multiprocessing import Pool
import os
import re
import socket
import sys
import time
import urllib2
import subprocess

import boto
import paramiko

EC2_INSTANCE_TYPE = 't1.micro'
STATE_FILENAME = os.path.expanduser('~/.bees')

# Utilities

def _read_server_list():
    instance_ids = []

    if not os.path.isfile(STATE_FILENAME):
        return (None, None, None)

    with open(STATE_FILENAME, 'r') as f:
        username = f.readline().strip()
        key_name = f.readline().strip()
        text = f.read()
        instance_ids = text.split('\n')

        print 'Read %i bees from the roster.' % len(instance_ids)

    return (username, key_name, instance_ids)

def _write_server_list(username, key_name, instances):
    with open(STATE_FILENAME, 'w') as f:
        f.write('%s\n' % username)
        f.write('%s\n' % key_name)
        f.write('\n'.join([instance.id for instance in instances]))

def _delete_server_list():
    os.remove(STATE_FILENAME)

def _get_pem_path(key):
    return os.path.expanduser('~/.ssh/%s.pem' % key)

# Methods

def up(count, group, zone, image_id, username, key_name):
    """
    Startup the load testing server.
    """
    existing_username, existing_key_name, instance_ids = _read_server_list()

    if instance_ids:
        print 'Bees are already assembled and awaiting orders.'
        return

    count = int(count)

    pem_path = _get_pem_path(key_name)

    if not os.path.isfile(pem_path):
        print 'No key file found at %s' % pem_path
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.connect_ec2()

    print 'Attempting to call up %i bees with image %s' % (count, image_id)

    reservation = ec2_connection.run_instances(
        image_id=image_id,
        min_count=count,
        max_count=count,
        key_name=key_name,
        security_groups=[group],
        instance_type=EC2_INSTANCE_TYPE,
        placement=zone)

    print 'Waiting for bees to load their machine guns...'

    instance_ids = []

    for instance in reservation.instances:
        while instance.state != 'running':
            print '.'
            time.sleep(5)
            instance.update()

        instance_ids.append(instance.id)

        print 'Bee %s is ready for the attack.' % instance.id

    ec2_connection.create_tags(instance_ids, { "Name": "a bee!" })

    _write_server_list(username, key_name, reservation.instances)

    print 'The swarm has assembled %i bees.' % len(reservation.instances)

def report():
    """
    Report the status of the load testing servers.
    """
    username, key_name, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    ec2_connection = boto.connect_ec2()

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    for instance in instances:
        print 'Bee %s: %s @ %s' % (instance.id, instance.state, instance.ip_address)

def down():
    """
    Shutdown the load testing server.
    """
    username, key_name, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.connect_ec2()

    print 'Calling off the swarm.'

    terminated_instance_ids = ec2_connection.terminate_instances(
        instance_ids=instance_ids)

    print 'Stood down %i bees.' % len(terminated_instance_ids)

    _delete_server_list()

def _attack(params):
    """
    Test the target URL with requests.

    Intended for use with multiprocessing.
    """
    print 'Bee %i is joining the swarm.' % params['i']

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            params['instance_name'],
            username=params['username'],
            key_filename=_get_pem_path(params['key_name']))

        print 'Bee %i is firing his machine gun. Bang bang!' % params['i']

        # stdin, stdout, stderr = client.exec_command('ab -r -n %(num_requests)s -c %(concurrent_requests)s -C "sessionid=NotARealSessionID" %(url)s' % params)
        if params['debug_mode']:
            command_params = ['node', '../websocket-test/load-test-client']
        else:
            command_params = ['node', 'load-test-client']

        command_params += ['--concurrent', '%(concurrent_requests)s' % params]
        command_params += ['--host', '%(host)s' % params]
        command_params += ['--port', '%(port)s' % params]
        if params['duration']:
            command_params += ['--duration', '%(duration)s' % params]
        else:
            command_params += ['--number', '%(num_requests)s' % params]
        if params['rate']:
            command_params += ['--rate', '%(rate)s' % params]
        if params['ramp_up_time']:
            command_params += ['--ramp_up_time', '%(ramp_up_time)s' % params]
        if params['no_ssl']:
            command_params.append('--no_ssl')

        if params['i'] == 0:
            print 'Running "%s" on bees' % (' '.join(command_params))

        if params['debug_mode']:
            # run command locally
            ab_results = subprocess.Popen(command_params, stdout=subprocess.PIPE).communicate()[0]
            print ab_results
        else:
            stdin, stdout, stderr = client.exec_command('cd ~/websocket-test && git pull origin && ' + ' '.join(command_params))
            ab_results = stdout.read()

        response = {}

        # ms_per_request_search = re.search('Time\ per\ request:\s+([0-9.]+)\ \[ms\]\ \(mean\)', ab_results)

        # requests_per_second_search = re.search('Requests\ per\ second:\s+([0-9.]+)\ \[#\/sec\]\ \(mean\)', ab_results)
        # fifty_percent_search = re.search('\s+50\%\s+([0-9]+)', ab_results)
        # ninety_percent_search = re.search('\s+90\%\s+([0-9]+)', ab_results)
        # complete_requests_search = re.search('Complete\ requests:\s+([0-9]+)', ab_results)

        # response['ms_per_request'] = float(ms_per_request_search.group(1))
        # response['requests_per_second'] = float(requests_per_second_search.group(1))
        # response['fifty_percent'] = float(fifty_percent_search.group(1))
        # response['ninety_percent'] = float(ninety_percent_search.group(1))
        # response['complete_requests'] = float(complete_requests_search.group(1))

        requests_per_second_last_minute = re.search('Average\ rate\ over\ last\ minute\ of\ ([0-9.]+)\ transactions\ per\ second', ab_results)
        requests_per_second_average = re.search('Average\ rate\ of\ ([0-9.]+)\ transactions\ per\ second', ab_results)
        request_count = re.search('([0-9.]+)\ connections\ opened', ab_results)
        ips = re.search('IPs\ used\:\ (.+)', ab_results)

        if not request_count:
            print 'Bee %i lost sight of the target (connection timed out).' % params['i']
            return None

        response['average_per_second'] = float(requests_per_second_average.group(1))
        response['last_minute_per_second'] = float(requests_per_second_last_minute.group(1))
        response['request_count'] = float(request_count.group(1))
        response['ips'] = ips.group(1).split(',')

        print 'Bee %i is out of ammo.' % params['i']

        client.close()

        return response
    except socket.error, e:
        print "Socket error"
        print e
        return e


def _print_results(results):
    """
    Print summarized load-testing results.
    """
    timeout_bees = [r for r in results if r is None]
    exception_bees = [r for r in results if type(r) == socket.error]
    complete_bees = [r for r in results if r is not None and type(r) != socket.error]

    num_timeout_bees = len(timeout_bees)
    num_exception_bees = len(exception_bees)
    num_complete_bees = len(complete_bees)

    if exception_bees:
        print '     %i of your bees didn\'t make it to the action. They might be taking a little longer than normal to find their machine guns, or may have been terminated without using "bees down".' % num_exception_bees

    if timeout_bees:
        print '     Target timed out without fully responding to %i bees.' % num_timeout_bees

    if num_complete_bees == 0:
        print '     No bees completed the mission. Apparently your bees are peace-loving hippies.'
        return

    complete_results = [r['request_count'] for r in complete_bees]
    total_complete_requests = sum(complete_results)
    print '     Complete requests:\t\t%i' % total_complete_requests

    complete_results = [r['average_per_second'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Average requests per second:\t%f [#/sec]' % mean_requests

    complete_results = [r['last_minute_per_second'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Average requests in the last minute per second:\t%f [#/sec]' % mean_requests

    complete_results = []
    for r in complete_bees:
        complete_results += r['ips']
    mean_requests = uniq(complete_results)
    print '     IPs used:\t%s' % (', '.join(mean_requests))

    print 'Mission Assessment: Swarm annihilated target.'


def attack(host, port, number, duration, concurrent, ramp_up_time, rate, no_ssl, debug_mode):
    """
    Test the root url of this site.
    """
    username, key_name, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees are ready to attack.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.connect_ec2()

    print 'Assembling bees.'

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    instance_count = len(instances)
    requests_per_instance = int(float(number) / instance_count)
    connections_per_instance = int(float(concurrent) / instance_count)
    rate_per_instance = int(float(rate) / instance_count)

    print 'Each of %i bees will fire %s rounds, %s at a time.' % (instance_count, requests_per_instance, connections_per_instance)

    params = []

    for i, instance in enumerate(instances):
        params.append({
            'i': i,
            'instance_id': instance.id,
            'instance_name': instance.public_dns_name,
            'host': host,
            'port': port,
            'concurrent_requests': connections_per_instance,
            'num_requests': requests_per_instance,
            'ramp_up_time': ramp_up_time,
            'rate': rate_per_instance,
            'duration': duration,
            'no_ssl': no_ssl,
            'username': username,
            'key_name': key_name,
            'debug_mode': debug_mode
        })

    print 'Stinging URL so it will be cached for the attack.'

    # Ping url so it will be cached for testing
    urllib2.urlopen('http://%s:%s/' % (host, port))

    print 'Organizing the swarm.'

    # Spin up processes for connecting to EC2 instances
    pool = Pool(len(params))
    results = pool.map(_attack, params)

    print 'Offensive complete.'

    _print_results(results)

    print 'The swarm is awaiting new orders.'


def uniq(seq):
    seen = set()
    seen_add = seen.add
    return [x for x in seq if x not in seen and not seen_add(x)]
