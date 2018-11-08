import os
import re
import pysubs2
import shutil
import pathlib
import paramiko
import chardet
import configparser
import json
import argparse

config = configparser.ConfigParser()
config.read('config.ini')


def detect_encoding(sub_path):
    raw_sub = open(sub_path, 'rb').read()
    encoding = chardet.detect(raw_sub)['encoding']
    if encoding == 'GB2312':
        encoding = 'GB18030'
    return encoding


def get_sub_name(media_name, sub_name, is_episode_number=False):
    if is_episode_number:
        return re.sub(config['FILE']['TV_EPISODE_PATTERN'], sub_name, media_name) + config['FILE']['NEW_SUB_EXTENSION']
    episode = get_tv_episode(sub_name)
    if episode:
        new_sub_name = re.sub(config['FILE']['TV_EPISODE_PATTERN'], episode, media_name) + config['FILE'][
            'NEW_SUB_EXTENSION']
    else:
        new_sub_name = media_name + config['FILE']['NEW_SUB_EXTENSION']
    return new_sub_name


def save_sub(sub, media_path, sub_path):
    media_name = os.path.splitext(os.path.basename(media_path))[0]
    sub_name = os.path.splitext(os.path.basename(sub_path))[0]
    new_sub_name = get_sub_name(media_name, sub_name)
    new_sub_path = os.path.join(os.path.dirname(sub_path), new_sub_name)
    sub.save(new_sub_path)
    return new_sub_path


def update_sub(sub):
    def ass_fs(font_size):
        return r'{\fs' + str(font_size) + r'}'

    def swap_upper_bottom(event):
        trans = event.plaintext.split("\n", maxsplit=1)
        upper = trans[0]
        bottom = trans[1]
        event.text = r"{0}\N{1}{2}".format(bottom, ass_fs(config['SUB']['BOTTOM_FS']), upper)

    def update_style():
        sub_default = pysubs2.load('default.ass')
        sub.import_styles(sub_default)

    def update_info():
        sub.info['ScaledBorderAndShadow'] = 'no'

    update_style()
    update_info()

    for e in sub.events:
        if e.text.find(r'\N') != -1 and e.text.find(r'{\pos') == -1:
            swap_upper_bottom(e)
    return sub


def handle_sub(sub_path, media_path):
    sub_encoding = detect_encoding(sub_path)
    sub = pysubs2.load(sub_path, sub_encoding)
    new_sub = update_sub(sub)
    return save_sub(new_sub, media_path, sub_path)


def is_video_file(filename):
    return os.path.splitext(os.path.basename(filename))[1] in json.loads(config['FILE']['MEDIA_FILE_EXTENSIONS'])


def get_ssh_client():
    ssh_config_file = os.path.expanduser("~/.ssh/config")
    ssh_config = paramiko.SSHConfig()
    ssh_config.parse(open(ssh_config_file, 'r'))
    lkup = ssh_config.lookup(config['SSH']['SSH_CONFIG_HOSTNAME'])

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.load_system_host_keys()
    ssh.connect(
        lkup['hostname'],
        username=lkup['user'],
        port=lkup['port']
    )
    return ssh


def single_sub_process(media_path, sub_path, sftp):
    """process single sub (flip subtitle lines, rename with media name, copy to remote server"""
    new_sub_local_path = handle_sub(sub_path, media_path)
    new_sub_remote_path = os.path.join(os.path.dirname(media_path), os.path.basename(new_sub_local_path))
    sftp.put(new_sub_local_path, new_sub_remote_path)


def get_tv_episode(filename):
    m = re.search(config['FILE']['TV_EPISODE_PATTERN'], filename)
    if m:
        return re.search(config['FILE']['TV_EPISODE_PATTERN'], filename)[0]
    else:
        return False


def get_tv_sub_dict(sub_dir):
    sub_files = os.listdir(sub_dir)
    sub_file_name_dict = {}
    for sub_name in sub_files:
        episode = get_tv_episode(sub_name)
        if episode and episode not in sub_file_name_dict:
            sub_file_name_dict[episode] = sub_name
    return sub_file_name_dict


def multi_subs_process(media_dir, sub_dir, sftp):
    """
    multiple subs in one folder (for TV), subtitle must contain episode number with format "S00E00"
    """
    media_files = sftp.listdir(media_dir)
    media_file_names = [f for f in media_files if is_video_file(f)]
    sub_file_name_dict = get_tv_sub_dict(sub_dir)
    for m in media_file_names:
        episode = get_tv_episode(m)
        if episode:
            media_path = os.path.join(media_dir, m)
            if episode in sub_file_name_dict:
                sub_path = os.path.join(sub_dir, sub_file_name_dict[episode])
                single_sub_process(media_path, sub_path, sftp)
            else:
                print("Episode {episode} subtitle is absent.".format(episode=episode))


def extract_files(input_idr, output_dir):
    """Extract files from subdirectories"""
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    with os.scandir(input_idr) as it:
        for e in it:
            if e.is_dir():
                with os.scandir(e.path) as sub_it:
                    for sub_e in sub_it:
                        if sub_e.is_file and re.search(config['FILE']['EXTRACT_PATTERN'], sub_e.name):
                            shutil.copy2(sub_e.path, output_dir)


def rename_files(sub_dir, season, init_episode=1):
    sub_files = sorted(os.listdir(sub_dir))
    for f in sub_files:
        new_name = 'S' + format(int(season), '02') + 'E' + format(init_episode, '02') + '.ass'
        os.rename(os.path.join(sub_dir, f), os.path.join(sub_dir, new_name))
        init_episode += 1


def main():
    parser = argparse.ArgumentParser(description='Process subtitles')
    subparsers = parser.add_subparsers(dest='subparser_name')

    parser_single = subparsers.add_parser('single',
                                          help="process single sub (flip subtitle lines, rename with media name, copy to remote server")
    parser_single.add_argument("-m", "--media_path", required=True)
    parser_single.add_argument("-s", "--sub_path", required=True)

    parser_multi = subparsers.add_parser('multi',
                                         help='multiple subs in one folder (for TV), subtitle must contain episode number with format "S00E00"')
    parser_multi.add_argument("-m", "--media_dir", required=True)
    parser_multi.add_argument("-s", "--sub_dir", required=True)

    parser_extract = subparsers.add_parser('extract',
                                           help='Extract files from subdirectories follow EXTRACT_PATTERN in config.ini')
    parser_extract.add_argument("-i", "--input", required=True)
    parser_extract.add_argument("-o", "--output", required=True)

    parser_rename = subparsers.add_parser('rename',
                                          help='Rename subtitle files in sequence')
    parser_rename.add_argument("-i", "--input", required=True)
    parser_rename.add_argument("-s", "--season", required=True, type=int)
    parser_rename.add_argument("-e", "--init_episode", default=1)

    args = parser.parse_args()

    if args.subparser_name == 'single':
        ssh = get_ssh_client()
        single_sub_process(args.media_path, args.sub_path, ssh.open_sftp())
        ssh.close()
    elif args.subparser_name == 'multi':
        ssh = get_ssh_client()
        multi_subs_process(args.media_dir, args.sub_dir, ssh.open_sftp())
        ssh.close()
    elif args.subparser_name == 'extract':
        extract_files(args.input, args.output)
    elif args.subparser_name == 'rename':
        rename_files(args.input, args.season, args.init_episode)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()