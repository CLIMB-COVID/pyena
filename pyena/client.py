import os
import argparse
import sys
import requests
from ftplib import FTP, FTP_TLS
from socket import timeout
from datetime import datetime
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup as bs         # pip install bs4 lxml
import json

from .util import hashfile

WEBIN_USER = os.environ.get('WEBIN_USER')
WEBIN_PASS = os.environ.get('WEBIN_PASS')

def _convert_library_strategy(s):
    conversions = {
        "TARGETED_CAPTURE": "Targeted-Capture",
    }
    if s in conversions:
        return conversions[s]
    return s

def get_sample_list(project, samp_name):
    r = requests.get(
        f"https://www.ebi.ac.uk/ena/portal/api/search?query=study_accession=%22{project}%22%20AND%20sample_alias=%22{samp_name}%22&result=sample&fields=sample_accession,sample_description,sample_alias,secondary_sample_accession&limit=0&download=false&format=json"
    )
    return json.loads(r.text)

def _convert_platform(instrument_name):
    # Based on https://github.com/enasequence/schema/blob/master/src/main/resources/uk/ac/ebi/ena/sra/schema/SRA.common.xsd
    valid_enums = {
        "ILLUMINA": {
            "X Five": "HiSeq X Five",
            "X Ten": "HiSeq X Ten",
            "Genome Analyzer": "Illumina Genome Analyzer",
            "Genome Analyzer II": "Illumina Genome Analyzer II",
            "Genome Analyzer IIx": "Illumina Genome Analyzer IIx",
            "HiScanSQ": "Illumina HiScanSQ",
            "HiSeq 1000": "Illumina HiSeq 1000",
            "HiSeq 1500": "Illumina HiSeq 1500",
            "HiSeq 2000": "Illumina HiSeq 2000",
            "HiSeq 2500": "Illumina HiSeq 2500",
            "HiSeq 3000": "Illumina HiSeq 3000",
            "HiSeq 4000": "Illumina HiSeq 4000",
            "iSeq 100": "Illumina iSeq 100",
            "MiSeq": "Illumina MiSeq",
            "MiniSeq": "Illumina MiniSeq",
            "NovaSeq 6000": "Illumina NovaSeq 6000",
            "NextSeq 500": "NextSeq 500",
            "NextSeq 550": "NextSeq 550",
            "hiseq": "unspecified", # catch all
            "miseq": "unspecified", # catch all
            "iseq": "unspecified", # catch all
            "novaseq": "unspecified", # catch all
            "nextseq": "unspecified", # catch all
        },
        "OXFORD_NANOPORE": {
            "MinION": "MinION",
            "GridION": "GridION",
            "PromethION": "PromethION",
        },
        "ION_TORRENT": {
            "Ion Torrent PGM": "Ion Torrent PGM",
            "Ion Torrent Proton": "Ion Torrent Proton",
            "Ion Torrent S5": "Ion Torrent S5",
            "Ion Torrent S5 XL": "Ion Torrent S5 XL",
        },
    }

    instrument_name = instrument_name.replace('_', ' ').lower()
    for instrument_make, instrument_models in valid_enums.items():
        for possible_model_k, possible_model_v in instrument_models.items():
            if possible_model_k.lower() in instrument_name:
                return instrument_make, possible_model_v
    return None, None

def _add_today(center_name, modify=False):
    if modify:
        action = '''
        <ACTION>
            <MODIFY/>
        </ACTION>'''
        # <ACTION>
        #     <VALIDATE/>
        # </ACTION>        
    else:
        action = '''
        <ACTION>
            <ADD/>
        </ACTION>
        <ACTION>
            <HOLD HoldUntilDate="%s" />
        </ACTION>
         ''' % datetime.today().strftime('%Y-%m-%d')
    return '''
    <SUBMISSION center_name="''' + center_name + '''">
    <ACTIONS>''' + action + '''
    </ACTIONS>
    </SUBMISSION>
    '''

def _release_target(target, center_name, real=False):
    release_xml = '''
    <SUBMISSION center_name="''' + center_name + '''">
    <ACTIONS>
        <ACTION>
            <RELEASE target="%s" />
        </ACTION>
    </ACTIONS>
    </SUBMISSION>
    ''' % target
    # print(release_xml)
    if real:
        return requests.post("https://www.ebi.ac.uk/ena/submit/drop-box/submit/",
                files={
                    'SUBMISSION': release_xml,
                }, auth=HTTPBasicAuth(WEBIN_USER, WEBIN_PASS))
    else:
        return requests.post("https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit/",
                files={
                    'SUBMISSION': release_xml,
                }, auth=HTTPBasicAuth(WEBIN_USER, WEBIN_PASS))

def status_code(response_text):
    response = 0
    return response

def handle_response(status_code, content, accession=False):
    """Returns -1 if the response failed entirely, 0 if appears OK, and 1 if appears incorrect"""
    response_code = -1
    response_accession = None

    if status_code != 200:
        # NOT 200
        sys.stderr.write("\n".join([
            '*' * 80,
            "ENA responded with HTTP %s." % status_code,
            "I don't know how to handle this. For your information, the response is below:",
            '*' * 80,
            content,
            '*' * 80,
            ]))
        response_code = -1
    else:
        # OK 200
        soup = bs(content, 'xml')
        if len(soup.findAll("ERROR")) > 0:
            # See if this is a duplicate accession before blowing up
            for error in soup.findAll("ERROR"):
                if "already exists in the submission account with accession:" in error.text:
                    response_accession = error.text.split()[-1].replace('"', "").replace('.', "")
                    response_code = 1
                    sys.stderr.write("[SKIP] Accession %s already exists. Moving on...\n" % response_accession)
                    break
                elif "has already been submitted and is waiting to be processed" in error.text:
                    #response_accession = error.text.split()[1].replace("object(", "").replace(")", "")
                    response_accession = error.text.split()[4]
                    response_code = 1
                    sys.stderr.write("[SKIP] File %s already uploaded. Cannot release again. Moving on...\n" % response_accession)
                    break
                elif "does not exist in the upload area" in error.text:
                    response_code = -3
                    break
            if not response_accession and response_code == -1:
                sys.stderr.write("\n".join([
                    '*' * 80,
                    "ENA responded with HTTP 200, but there were ERROR messages in the response.",
                    "I don't know how to handle this. For your information, the response is below:",
                    '*' * 80,
                    content,
                    '*' * 80,
                ]))
                response_code = -1
        else:
            if accession:
                # Try and parse an accession
                try:
                    response_accession = soup.find(accession).get("accession")
                except:
                    pass
            response_code = 0

    return response_code, response_accession


def submit_today(submit_type, payload, center_name, release_asap=False, real=False, modify=False):
    files = {}
    files[submit_type] = payload
    files["SUBMISSION"] = _add_today(center_name, modify)
    # print(payload)
    # print(files["SUBMISSION"])
    
    if real:
        r = requests.post("https://www.ebi.ac.uk/ena/submit/drop-box/submit/",
                files=files,
                auth=HTTPBasicAuth(WEBIN_USER, WEBIN_PASS))
    else:
        r = requests.post("https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit/",
                files=files,
                auth=HTTPBasicAuth(WEBIN_USER, WEBIN_PASS))
        # print(r.text)
        
    status, accession = handle_response(r.status_code, r.text, accession=submit_type)
    if release_asap and status == 0:
        r = _release_target(accession, center_name, real=real)
        # print(r.text)
        status, _ = handle_response(r.status_code, r.text)
        if status == 0:
            sys.stderr.write("[INFO] %s released successfully: %s\n" % (submit_type, accession))

    return status, accession

def register_sample(sample_alias, taxon_id, center_name, attributes={}, real=False, modify=False):
    s_attributes = "\n".join(["<SAMPLE_ATTRIBUTE><TAG>%s</TAG><VALUE>%s</VALUE></SAMPLE_ATTRIBUTE>" % (k, v) for k,v in attributes.items() if v is not None and len(v) > 0])

    s_xml = '''
    <SAMPLE_SET>
    <SAMPLE alias="''' + sample_alias + '''" center_name="''' + center_name + '''">
    <TITLE>''' + sample_alias + '''</TITLE>
    <SAMPLE_NAME>
      <TAXON_ID>''' + taxon_id + '''</TAXON_ID>
    </SAMPLE_NAME>
    <SAMPLE_ATTRIBUTES>''' + s_attributes + '''</SAMPLE_ATTRIBUTES>
    </SAMPLE>
    </SAMPLE_SET>
    '''
    if modify:
        return submit_today("SAMPLE", s_xml, center_name, release_asap=False, real=real, modify=modify)
    else:
        return submit_today("SAMPLE", s_xml, center_name, release_asap=True, real=real, modify=modify)

def register_experiment(exp_alias, study_accession, sample_accession, instrument, library_d, center_name, attributes={}, real=False):
    e_attributes = "\n".join(["<EXPERIMENT_ATTRIBUTE><TAG>%s</TAG><VALUE>%s</VALUE></EXPERIMENT_ATTRIBUTE>" % (k, v) for k,v in attributes.items() if v is not None and len(v) > 0])

    platform, model = _convert_platform(instrument)
    if platform:
        platform_stanza = "<%s><INSTRUMENT_MODEL>%s</INSTRUMENT_MODEL></%s>" % (platform, model, platform)
    else:
        #sys.stderr.write("[FAIL] Unable to construct platform stanza for experiment %s with instrument %s\n" % (exp_alias, instrument))
        #return -1, None
        platform_stanza = ""

    pair_size = 0
    layout_stanza = []

    if pair_size:
        layout_stanza.append("<PAIRED />") # NOMINAL_LENGTH=\"%d\"/>" % pair_size)
    else:
        layout_stanza.append("<SINGLE />")

    e_protocol = ""
    if library_d["protocol"]:
        e_protocol = "<LIBRARY_CONSTRUCTION_PROTOCOL>%s</LIBRARY_CONSTRUCTION_PROTOCOL>" % library_d["protocol"]

    e_xml = '''
    <EXPERIMENT_SET>
    <EXPERIMENT alias="''' + exp_alias + '''" center_name="''' + center_name + '''">
       <TITLE>''' + exp_alias + '''</TITLE>
       <STUDY_REF accession="''' + study_accession + '''"/>
       <DESIGN>
           <DESIGN_DESCRIPTION/>
           <SAMPLE_DESCRIPTOR accession="''' + sample_accession + '''"/>
           <LIBRARY_DESCRIPTOR>
               <LIBRARY_NAME/>
               <LIBRARY_STRATEGY>''' + library_d["strategy"] + '''</LIBRARY_STRATEGY>
               <LIBRARY_SOURCE>''' + library_d["source"] + '''</LIBRARY_SOURCE>
               <LIBRARY_SELECTION>''' + library_d["selection"] + '''</LIBRARY_SELECTION>
               <LIBRARY_LAYOUT>''' + "".join(layout_stanza) + '''</LIBRARY_LAYOUT>
           ''' + e_protocol + '''
           </LIBRARY_DESCRIPTOR>
       </DESIGN>
       <PLATFORM>''' + platform_stanza + '''
       </PLATFORM>
       <EXPERIMENT_ATTRIBUTES>''' + e_attributes + '''</EXPERIMENT_ATTRIBUTES>
    </EXPERIMENT>
    </EXPERIMENT_SET>
    '''

    # Register experiment to add run to
    return submit_today("EXPERIMENT", e_xml, center_name, release_asap=True, real=real)

def register_run(run_alias, fn, exp_accession, center_name, fn_type="bam", real=False, upload=True):

    if upload:
        try:
            ftp = FTP('webin.ebi.ac.uk', user=WEBIN_USER, passwd=WEBIN_PASS, timeout=30)
            ftp.storbinary('STOR %s' % os.path.basename(fn), open(fn, 'rb'))
            ftp.quit()
        except Exception as e:
            sys.stderr.write("[FAIL] FTP transfer timed out or failed for %s\n%s" % (fn, e))
            return -1, None

    fn_checksum = hashfile(fn)

    r_xml = '''
    <RUN_SET>
        <RUN alias="''' + run_alias + '''" center_name="''' + center_name + '''">
            <EXPERIMENT_REF accession="''' + exp_accession + '''"/>
            <DATA_BLOCK>
                <FILES>
                    <FILE filename="''' + os.path.basename(fn) + '''" filetype="''' + fn_type + '''" checksum_method="MD5" checksum="''' + fn_checksum + '''" />
                </FILES>
            </DATA_BLOCK>
        </RUN>
    </RUN_SET>
    '''
    return submit_today("RUN", r_xml, center_name, release_asap=True, real=real)

def cli():
    parser = argparse.ArgumentParser()

    parser.add_argument("--my-data-is-ready", action="store_true")
    parser.add_argument("--no-ftp", action="store_true")
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--modify", action="store_true")

    parser.add_argument("--study-accession", required=True)

    parser.add_argument("--sample-attr", action='append', nargs=2, metavar=('tag', 'value'))
    parser.add_argument("--sample-name", required=True)
    parser.add_argument("--sample-center-name", required=True)
    parser.add_argument("--sample-taxon", required=True)

    parser.add_argument("--experiment-attr", action='append', nargs=2, metavar=('tag', 'value'))

    parser.add_argument("--run-name", required=False)
    parser.add_argument("--run-file-path", required=False)
    parser.add_argument("--run-file-type", required=False, default="bam")
    parser.add_argument("--run-center-name", required=False)
    parser.add_argument("--run-instrument", required=False)
    parser.add_argument("--run-lib-source", required=False)
    parser.add_argument("--run-lib-selection", required=False)
    parser.add_argument("--run-lib-strategy", required=False)
    parser.add_argument("--run-lib-protocol", required=False, default="")


    args = parser.parse_args()



    sample_accession = exp_accession = run_accession = None
    success = 0

    #Check if the sample / Project combo already exists without crashing ENA
    samp_list = get_sample_list(args.study_accession, args.sample_name)

    if samp_list:
        sys.stderr.write("[SKIP] Accession %s already exists. Moving on...\n" % samp_list[0]["secondary_sample_accession"])
        sample_accession = samp_list[0]["secondary_sample_accession"]
        sample_stat = 1
    else:
        sample_stat, sample_accession = register_sample(args.sample_name, args.sample_taxon, args.sample_center_name, {x[0]: x[1] for x in args.sample_attr}, real=args.my_data_is_ready, modify=args.modify)
    
    if sample_stat >= 0 and sample_accession and args.sample_only:
        success = 1
    elif sample_stat >= 0 and not args.sample_only: # Only register_experiment / run if sample only flag not set
        exp_stat, exp_accession = register_experiment(args.run_name, args.study_accession, sample_accession, args.run_instrument.replace("_", " "), attributes={x[0]: x[1] for x in args.experiment_attr}, library_d={
            "source": args.run_lib_source.replace("_", " "),
            "selection": args.run_lib_selection.replace("_", " "),
            "strategy": _convert_library_strategy(args.run_lib_strategy),
            "protocol": args.run_lib_protocol,
        }, center_name=args.run_center_name, real=args.my_data_is_ready)
        if exp_stat >= 0:
            do_upload = False if args.no_ftp else True
            run_stat, run_accession = register_run(args.run_name, args.run_file_path, exp_accession, center_name=args.run_center_name, fn_type=args.run_file_type, real=args.my_data_is_ready, upload=do_upload)
            if run_stat >= 0 and run_accession:
                success = 1

    sys.stdout.write(" ".join([str(x) for x in [
        success,
        1 if args.my_data_is_ready else 0,
        args.sample_name,
        args.run_name,
        args.run_file_path,
        args.study_accession,
        sample_accession,
        exp_accession,
        run_accession
    ]]) + '\n')
    if not success:
        if args.sample_only:
            sys.exit(2)
        if run_stat < 0:
            sys.exit(abs(run_stat))
        sys.exit(2)
