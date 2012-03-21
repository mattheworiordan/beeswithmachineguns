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
import httplib, urllib
import time

from debug_instance import DebugInstance

STATE_FILENAME = os.path.expanduser('~/.bees')
MAX_HTTP_ERRORS = 10

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

def up(count, group, zone, image_id, username, key_name, instance_type):
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
        instance_type=instance_type,
        placement=zone)

    print 'Waiting for bees to load their machine guns...'

    instance_ids = []

    for instance in reservation.instances:
        instance.update()
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
        querystring_params = urllib.urlencode({
            'host': params['host'],
            'port': params['port'],
            'concurrent': params['concurrent_requests'],
            'number': params['num_requests'],
            'ramp_up_time': params['ramp_up_time'] if params['ramp_up_time'] else '',
            'no_ssl': 'true' if params['no_ssl'] else '',
            'rate': params['rate'] if params['rate'] else '',
            'duration': params['duration'] if params['duration'] else ''
            })
        headers = {"Accept": "text/plain"}
        if params['debug_mode']:
            port = 8001
        else:
            port = 8000

        attack_url = "/start?%s" % querystring_params

        if params['i'] == 0:
            print 'Attack URL: %s' % attack_url

        conn = httplib.HTTPConnection('%s:%s' % (params['instance_name'], port))
        conn.request("GET", attack_url, None, headers)
        response = conn.getresponse()
        response_data = response.read()

        if re.search('Load test ([0-9.]+) already running', response_data):
            print 'Bee %i is already running a load test' % params['i']
            return None

        load_test_id = re.search('Started load test number ([0-9.]+)', response_data)
        if not load_test_id:
            print 'Bee %i appears to be offline and has not started the load test' % params['i']
            print 'Response: %s' % response_data
            return None
        else:
            load_test_id = int(load_test_id.group(1))

        print 'Bee %i is firing his machine gun (load test #%i, host: %s). Bang bang!' % (params['i'], load_test_id, params['instance_name'])

        http_errors = 0
        while http_errors < MAX_HTTP_ERRORS:
            try:
                conn = httplib.HTTPConnection('%s:%s' % (params['instance_name'], port))
                conn.request("GET", "/report", None, headers)
                response = conn.getresponse()
                response_data = response.read()
                if re.search('Report for load test %i not ready yet' % load_test_id, response_data):
                    time.sleep(3)
                elif re.search('Report for load test %i complete' % load_test_id, response_data):
                    break
                else:
                    print 'Bee %i is not responding to report requests correctly' % params['i']
                    print 'Response for load test %i: %s' % (load_test_id, response_data)
                    return None
            except:
                http_errors += 1
                if http_errors == MAX_HTTP_ERRORS:
                    print 'Bee %i is unresponsive and has suffered %i http errors' % (params['i'], MAX_HTTP_ERRORS)
                    return None
                time.sleep(1)

        response = {}

        requests_per_second_last_minute = re.search('Average\ rate\ over\ last\ minute\ of\ ([0-9.]+)\ transactions\ per\ second', response_data)
        requests_per_second_average = re.search('Average\ rate\ of\ ([0-9.]+)\ transactions\ per\ second', response_data)
        request_count = re.search('([0-9.]+)\ connections\ opened', response_data)
        error_count = re.search('Load test errors ([0-9.]+)', response_data)
        error_rate_per_minute = re.search('errors per minute ([0-9.]+)', response_data)
        ips = re.search('IPs\ used\:\ (.+)', response_data)
        re_matcher = re.compile('Seconds passed.*Actual Messages\n(.*)\n\-\-\-', re.MULTILINE|re.DOTALL)
        report = re_matcher.search(response_data)

        if not request_count:
            print 'Bee %i lost sight of the target (connection timed out).' % params['i']
            print 'Response was: %s' % response_data
            return None

        response['average_per_second'] = float(requests_per_second_average.group(1))
        response['last_minute_per_second'] = float(requests_per_second_last_minute.group(1))
        response['request_count'] = float(request_count.group(1))
        response['error_count'] = float(error_count.group(1))
        response['error_rate_per_minute'] = float(error_rate_per_minute.group(1))
        response['ips'] = ips.group(1).split(',')
        response['report'] = report.group(1).split('\n')

        print 'Bee %i is out of ammo.' % params['i']

        return response
    except socket.error, e:
        print "Socket error for host %s" % params['host']
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

    complete_results = [r['error_count'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Total errors encountered by bees:\t%f [#/sec]' % mean_requests

    complete_results = [r['error_rate_per_minute'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Average errors per minute by bees:\t%f [#/sec]' % mean_requests

    complete_results = []
    for r in complete_bees:
        complete_results += r['ips']
    mean_requests = uniq(complete_results)
    print '     IPs used:\t%s' % (', '.join(mean_requests))

    complete_results = {}
    for r in complete_bees:
        for row in r['report']:
            row = row.split(',')
            seconds = int(row[0])
            if seconds in complete_results:
                complete_results[seconds][0] += int(row[1])
                complete_results[seconds][1] += int(row[2])
                if row[3] != 'max':
                    complete_results[seconds][2] += int(row[3])
                complete_results[seconds][3] += int(row[4])
            else:
                messages_attempted = row[3] if row[3] == 'max' else int(row[3])
                complete_results[seconds] = [int(row[1]), int(row[2]), messages_attempted, int(row[4])]
    print '\nCollective bee performance report:\nSeconds passed,Connections attempted,Actual connections,Messages attempted,Actual Messages'
    for i in sorted(complete_results.keys()):
        row = complete_results[i]
        print '%s,%s' % (i, ','.join(str(i) for i in row))

    print '\nMission Assessment: Swarm annihilated target.'


def attack(host, port, number, duration, concurrent, ramp_up_time, rate, no_ssl, debug_mode):
    """
    Test the root url of this site.
    """

    if debug_mode:
        username, key_name, instance_ids = 'ubuntu', 'aws', True
        print 'Running in debug mode, executing locally for one instance on port 8001'
    else:
        username, key_name, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees are ready to attack.'
        return

    if debug_mode:
        instances = [DebugInstance('1', 'localhost')]
    else:
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
    if rate:
        rate_per_instance = int(float(rate) / instance_count)
    else:
        rate_per_instance = False

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
    if no_ssl:
        ssl_suffix = ''
    else:
        ssl_suffix = 's'

    hosts_array = host.split(',')
    for i, host in enumerate(hosts_array):
        print '   sting url %i: http%s://%s:%s/' % (i, ssl_suffix, host, port)
        urllib2.urlopen('http%s://%s:%s/' % (ssl_suffix, host, port))

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
