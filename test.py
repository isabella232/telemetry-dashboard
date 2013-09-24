try:
    import simplejson as json
except ImportError:
    import json
import math
import sys

# Import histogram specs and generated by makefile using specgen.py
import specs

verbose = True

# Auxiliary method to write log messages
def log(msg):
    if verbose:
        print >> sys.stderr, msg

# Auxiliary method for computing bucket offsets from parameters, it is stolen
# from histogram_tools.py, though slightly modified...
def exponential_buckets(dmin, dmax, n_buckets):
    log_max = math.log(dmax);
    ret_array = [0] * n_buckets
    current = dmin
    ret_array[1] = current
    for bucket_index in range(2, n_buckets):
        log_current = math.log(current)
        log_ratio = (log_max - log_current) / (n_buckets - bucket_index)
        log_next = log_current + log_ratio
        next_value = int(math.floor(math.exp(log_next) + 0.5))
        if next_value > current:
            current = next_value
        else:
            current = current + 1
        ret_array[bucket_index] = current
    return ret_array

# Create buckets from buckets2index from ranges... snippet pretty much stolen
# from specgen.py
def buckets2index_from_ranges(ranges):
    buckets = map(str, ranges)
    bucket2index = {}
    for i in range(0, len(buckets)):
        bucket2index[buckets[i]] = i
    return bucket2index

# Bucket offsets for simple measures
simple_measures_buckets = (
                           buckets2index_from_ranges(
                                            exponential_buckets(1, 30000, 50)),
                           exponential_buckets(1, 30000, 50)
                           )

# histogram incoming format:
#   [
#       bucket0, bucket1, ..., bucketN,
#       sum, log_sum, log_sum_squares, sum_squares_lo, sum_squares_hi
#   ]
# Aggregated histogram format:
#   [
#       bucket0, bucket1, ..., bucketN,
#       sum, log_sum, log_sum_squares, sum_squares_lo, sum_squares_hi, count
#   ]
# where count is the number of histograms aggregated in the histogram.

def map(key, dims, value, context):
    # Unpack dimensions
    reason, appName, channel, version, buildId, submissionDate = dims

    # Get the major version
    majorVersion = version.split('.')[0]

    # Get the build date, ignore the rest of the buildId
    buildDate = buildId[:8]

    # Load JSON payload
    payload = json.loads(value)

    # Get OS, osVersion and architecture information
    try:
        info = payload['info']
        OS = info['OS']
        osVersion = str(info['version'])
        arch = info['arch']
    except (KeyError, IndexError, UnicodeEncodeError):
        log("error while unpacking the payload")
        return

    # todo combine OS + osVersion + santize on crazy platforms like linux to
    #      reduce pointless choices
    if OS == "Linux":
        osVersion = osVersion[:3]

    # Create filter path
    filterPath = (buildDate, reason, appName, OS, osVersion, arch)

    # For each histogram
    for hgramName, hgramValues in payload.get('histograms', {}).iteritems():
        # Check that we have bucket information on this histogram
        bucket2index = specs.histograms.get(hgramName, None)
        if bucket2index == None:
            log("Missing bucket2index for %s" % hgramName)
            continue
        # Abort if bucket length doesn't match
        if len(hgramValues) == len(bucket2index[0]) + 5:
            context.write((channel, majorVersion, hgramName),
                          {filterPath: hgramValues + [1]})
    
    # Now read and output simple measures
    for name, value in payload.get('simpleMeasurements', {}).iteritems():
        # Handle cases where the value is a dictionary of simple measures
        if type(value) == dict:
            for subName, subValue in value.iteritems():
                map_simplemeasure(channel, majorVersion, filterPath,
                                  name + "_" + subName, subValue, context)
        else:
            map_simplemeasure(channel, majorVersion, filterPath, name, value,
                              context)

# Map a simple measure
def map_simplemeasure(channel, majorVersion, filterPath, name, value, context):
    # Sanity check value
    if type(value) not in (int, long):
        log("%s is not a value type for simpleMeasurements \"%s\"" %
            (type(value), name))

    bucket = simple_measures_buckets[1]
    outarray = [0] * (len(bucket) + 6)
    for i in reversed(range(0, len(bucket))):
        if value >= bucket[i]:
            outarray[i] = 1
            break

    log_val = math.log(math.fabs(value) + 1)
    outarray[-6] = value                # sum
    outarray[-5] = log_val              # log_sum
    outarray[-4] = log_val * log_val    # log_sum_squares
    outarray[-3] = 0                    # sum_squares_lo
    outarray[-2] = 0                    # sum_squares_hi
    outarray[-1] = 1                    # count

    # Output result array
    context.write((channel, majorVersion, "SIMPLE_MEASURES_" + name.upper()), 
                  {filterPath: outarray})


def map_finished(context):
    log("Finally got to map_finished!!!")

def commonCombine(values):
    output = {}
    for d in values:
        for filterPath, hgramValues in d.iteritems():
            existing = output.get(filterPath, None)
            if existing is None:
                output[filterPath] = hgramValues
                continue
            for y in xrange(0, len(hgramValues)):
                existing[y] += (hgramValues[y] or 0)
    return output

def reduce(key, values, context):
    # Produce output ready for json serialization
    output = {}
    for filterPath, hgramValues in commonCombine(values).iteritems():
        output["/".join(filterPath)] = hgramValues

    # Get histogram name
    hgramName = key[2]
    if hgramName.startswith("SIMPLE_MEASURES_"):
        buckets = simple_measures_buckets[1];
    else:
        buckets = specs.histograms.get(hgramName)[1]

    # Write final output
    final_out = {
        'buckets':  buckets,
        'values':   output
    }
    context.write("/".join(key), json.dumps(final_out))
