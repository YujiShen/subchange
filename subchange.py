import os
from pathlib import Path
import re
import shutil
import configparser
import json
import argparse
from collections import defaultdict
import logging
from typing import Union

import pysubs2
import paramiko
import chardet


config = configparser.ConfigParser()
config.read("config.ini")


class Sub:
    def __init__(self, sub_path):
        self.sub_path = sub_path
        self.sub = pysubs2.load(sub_path, self.detect_encoding())

    def detect_encoding(self):
        raw_sub = open(self.sub_path, "rb").read()
        encoding = chardet.detect(raw_sub)["encoding"]
        if encoding == "GB2312":
            encoding = "GB18030"
        self.encoding = encoding
        return self.encoding

    def update_style(self):
        for key, value in self.sub.styles.items():
            self.sub.styles[key].fontsize = config["SUB"]["OTHER_FS"]
        sub_default = pysubs2.load("default.ass")
        self.sub.import_styles(sub_default)

    def update_info(self):
        self.sub.info["ScaledBorderAndShadow"] = "no"
        if "PlayResX" in self.sub.info:
            del self.sub.info["PlayResX"]
        if "PlayResY" in self.sub.info:
            del self.sub.info["PlayResY"]

    @staticmethod
    def change_inline_fs(sub_event):
        sub_event.text = re.sub(
            r"(?<=\\\\fs)\d+(?=[\\\\}])", str(config["SUB"]["OTHER_FS"]), sub_event.text
        )

    @staticmethod
    def swap_event(sub_event):
        """
        Swap an event up and bottom line split by new line
        """
        trans = sub_event.plaintext.split("\n", maxsplit=1)
        upper = trans[0]
        bottom = trans[1]
        sub_event.text = r"{0}\N{1}{2}".format(
            bottom, r"{\fs" + str(config["SUB"]["BOTTOM_FS"]) + r"}", upper
        )
        sub_event.style = "Default"

    def save(self, save_path=None):
        if save_path is None:
            self.sub.save(self.sub_path)
        else:
            self.sub.save(save_path)

    def swap(self):
        for e in self.sub.events:
            if e.text.find(r"\N") != -1 and e.text.find(r"{\pos") == -1:
                self.swap_event(e)
            else:
                self.change_inline_fs(e)

    def shift(self, ms):
        self.sub.shift(ms=ms)
        self.save()

    def recode(self):
        self.save()


class MediaFile:
    def __init__(self, media_path):
        self.media_path = Path(media_path)

    @staticmethod
    def get_sub_lang_short(sub_path: Path):
        if re.search("简体", Path(sub_path).name):
            return "zh"
        else:
            return "en"

    def get_sub_path(self, sub_path: Path, sub_lang: str = None) -> Path:
        if sub_lang is None:
            sub_lang = self.get_sub_lang_short(sub_path)

        sub_new_path = Path(
            self.media_path.parent,
            self.media_path.stem + "." + sub_lang + sub_path.suffix,
        )
        return sub_new_path

    def get_sub(self, sub: Union[Sub, str], sub_lang: str = None):
        if isinstance(sub, Sub):
            sub_path = Path(sub.sub_path)
            sub_new_path = self.get_sub_path(sub_path, sub_lang=sub_lang)
            sub.save(sub_new_path)
        else:
            sub_path = Path(sub_path)
            sub_new_path = self.get_sub_path(sub_path, sub_lang=sub_lang)
            shutil.copy(str(sub_path), str(sub_new_path))

        logging.info(f"Copy sub to {sub_new_path}")


def detect_encoding(sub_path):
    raw_sub = open(sub_path, "rb").read()
    encoding = chardet.detect(raw_sub)["encoding"]
    if encoding == "GB2312":
        encoding = "GB18030"
    return encoding


def get_sub_name(media_name, sub_name, lang=None):
    episode = get_tv_episode(media_name, False)
    if episode:
        new_sub_name = (
            re.sub(
                config["FILE"]["TV_EPISODE_PATTERN"],
                episode,
                media_name,
                flags=re.IGNORECASE,
            )
            + config["FILE"]["NEW_SUB_EXTENSION"]
        )
    else:
        new_sub_name = media_name + config["FILE"]["NEW_SUB_EXTENSION"]
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
        return r"{\fs" + str(font_size) + r"}"

    def swap_upper_bottom(event):
        trans = event.plaintext.split("\n", maxsplit=1)
        upper = trans[0]
        bottom = trans[1]
        event.text = r"{0}\N{1}{2}".format(
            bottom, ass_fs(config["SUB"]["BOTTOM_FS"]), upper
        )
        event.style = "Default"

    def update_style():
        for key, value in sub.styles.items():
            sub.styles[key].fontsize = config["SUB"]["OTHER_FS"]
        sub_default = pysubs2.load("default.ass")
        sub.import_styles(sub_default)

    def update_info():
        sub.info["ScaledBorderAndShadow"] = "no"
        if "PlayResX" in sub.info:
            del sub.info["PlayResX"]
        if "PlayResY" in sub.info:
            del sub.info["PlayResY"]

    def change_inline_fs(event):
        event.text = re.sub(
            r"(?<=\\\\fs)\d+(?=[\\\\}])", str(config["SUB"]["OTHER_FS"]), event.text
        )

    update_style()
    update_info()

    for e in sub.events:
        if e.text.find(r"\N") != -1 and e.text.find(r"{\pos") == -1:
            swap_upper_bottom(e)
        else:
            change_inline_fs(e)
    return sub


def handle_sub(sub_path, media_path, update):
    sub_encoding = detect_encoding(sub_path)
    sub = pysubs2.load(sub_path, sub_encoding)
    if update:
        new_sub = update_sub(sub)
    else:
        new_sub = sub
    return save_sub(new_sub, media_path, sub_path)


def is_video_file(filename):
    return os.path.splitext(os.path.basename(filename))[1] in json.loads(
        config["FILE"]["MEDIA_FILE_EXTENSIONS"]
    )


def is_sub_file(filename):
    return os.path.splitext(os.path.basename(filename))[1] in json.loads(
        config["SUB"]["SUB_FILE_EXTENSIONS"]
    )


def get_ssh_client():
    ssh_config_file = os.path.expanduser("~/.ssh/config")
    ssh_config = paramiko.SSHConfig()
    ssh_config.parse(open(ssh_config_file, "r"))
    lkup = ssh_config.lookup(config["SSH"]["SSH_CONFIG_HOSTNAME"])

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.load_system_host_keys()
    ssh.connect(lkup["hostname"], username=lkup["user"], port=lkup["port"])
    return ssh


def single_sub_process(media_path, sub_path, sftp, update=True):
    """
    process single sub (flip subtitle lines, rename with media name,
    copy to remote server
    """
    new_sub_local_path = handle_sub(sub_path, media_path, update)
    new_sub_remote_path = os.path.join(
        os.path.dirname(media_path), os.path.basename(new_sub_local_path)
    )
    sftp.put(new_sub_local_path, new_sub_remote_path)


def get_tv_episode(filename, formatting=True):
    m = re.search(config["FILE"]["TV_EPISODE_PATTERN"], filename, re.IGNORECASE)
    if m:
        if formatting:
            return m[0].upper().replace(".", "")
        else:
            return m[0]
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


def get_tv_sub_dict_list(media_dir):
    medias = os.listdir(media_dir)
    sub_files = [f for f in medias if is_sub_file(f)]
    sub_file_name_dict_list = defaultdict(list)
    for sub_name in sub_files:
        episode = get_tv_episode(sub_name)
        if episode and episode:
            sub_file_name_dict_list[episode].append(sub_name)
    return sub_file_name_dict_list


def multi_subs_process(media_dir, sub_dir, sftp, update=True):
    """
    multiple subs in one folder (for TV),
    subtitle must contain episode number with format "S00E00"
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
                single_sub_process(media_path, sub_path, sftp, update)
            else:
                print("Episode {episode} subtitle is absent.".format(episode=episode))


def extract_files(input_idr, output_dir, pattern):
    """Extract files from subdirectories"""
    if not pattern:
        pattern = config["FILE"]["EXTRACT_PATTERN"]
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with os.scandir(input_idr) as it:
        for e in it:
            if e.is_dir():
                with os.scandir(e.path) as sub_it:
                    for sub_e in sub_it:
                        if sub_e.is_file and re.search(pattern, sub_e.name):
                            shutil.copy2(sub_e.path, output_dir)


def rename_files(sub_dir, season, init_episode=1):
    sub_files = sorted(os.listdir(sub_dir))
    for f in sub_files:
        new_name = (
            "S" + format(int(season), "02") + "E" + format(init_episode, "02") + ".ass"
        )
        os.rename(os.path.join(sub_dir, f), os.path.join(sub_dir, new_name))
        init_episode += 1


def rename_from_file(sub_dir, name_file):
    sub_files = sorted(os.listdir(sub_dir))
    with open(name_file) as fp:
        for line_number, new_name in enumerate(fp):
            if not re.search(r".+\.\w+", new_name):
                raise Exception("Please add file extension!")
            new_name = new_name.rstrip()
            new_name = re.sub(r"\s{0,}:\s{0,}", " - ", new_name)
            new_name = re.sub(r"\s{0,}/\s{0,}", ", ", new_name)
            os.rename(
                os.path.join(sub_dir, sub_files[line_number]),
                os.path.join(sub_dir, new_name),
            )


# def transfer_sub(old_sub_path, new_media_path):
#     shutil.copy2(sub_e.path, output_dir)
#
#
# def transfer_subs(old_media_dir, new_media_dir):
#     old_sub_dict_list = get_tv_sub_dict_list(old_media_dir)
#     media_files = os.listdir(new_media_dir)
#     media_file_names = [f for f in media_files if is_video_file(f)]
#     for m in media_file_names:
#         episode = get_tv_episode(m)
#         if episode:
#             media_path = os.path.join(media_dir, m)
#             if episode in sub_file_name_dict:
#                 sub_path = os.path.join(sub_dir, sub_file_name_dict[episode])
#                 single_sub_process(media_path, sub_path, sftp)
#             else:
#                 print("Episode {episode} subtitle is absent.".format(episode=episode))


def merge_subs(sub_zh_path, sub_en_path):
    """
    Merge Chinese and English subtitles into one, using styles to distinguish them
    """
    sub_zh = pysubs2.load(sub_zh_path, detect_encoding(sub_zh_path))
    sub_en = pysubs2.load(sub_en_path)

    sub_default = pysubs2.load("default.ass")
    sub_zh.rename_style("Default", "Chinese")
    sub_zh.import_styles(sub_default)
    sub_en.rename_style("Default", "English")
    sub_zh.events = sub_zh.events + sub_en.events
    sub_zh.sort()
    for e in sub_zh.events:
        e.plaintext = e.plaintext.replace("\n", " - ")
    sub_zh.info["ScaledBorderAndShadow"] = "no"
    return sub_zh


def merge_single_subs(sub_zh_path, sub_en_path):
    new_sub = merge_subs(sub_zh_path, sub_en_path)
    new_sub_path = sub_zh_path + ".ass"
    new_sub.save(new_sub_path)
    return new_sub_path


def merge_multi_subs(sub_zh_dir, sub_en_dir):
    sub_zhs = os.listdir(sub_zh_dir)
    sub_zh_names = [f for f in sub_zhs if is_sub_file(f)]
    sub_en_name_dict = get_tv_sub_dict(sub_en_dir)
    for sub_zh_name in sub_zh_names:
        episode = get_tv_episode(sub_zh_name)
        if episode:
            sub_zh_path = os.path.join(sub_zh_dir, sub_zh_name)
            if episode in sub_en_name_dict:
                sub_en_path = os.path.join(sub_en_dir, sub_en_name_dict[episode])
                merge_single_subs(sub_zh_path, sub_en_path)
                # Remove original file for following transfer
                os.remove(sub_zh_path)
            else:
                print("Episode {episode} subtitle is absent.".format(episode=episode))


def shift_sub(sub_path, ms):
    print(sub_path)
    sub = pysubs2.load(sub_path, detect_encoding(sub_path))
    sub.shift(ms=ms)
    sub.save(sub_path)


def shift_subs(sub_dir, ms):
    sub_file_names = [f for f in os.listdir(sub_dir) if is_sub_file(f)]
    for sub_file_name in sub_file_names:
        sub_path = os.path.join(sub_dir, sub_file_name)
        shift_sub(sub_path, ms)


def recode_sub(sub_path):
    sub = pysubs2.load(sub_path, detect_encoding(sub_path))
    sub.save(sub_path)


def recode_subs(sub_dir):
    sub_file_names = [f for f in os.listdir(sub_dir) if is_sub_file(f)]
    for sub_file_name in sub_file_names:
        sub_path = os.path.join(sub_dir, sub_file_name)
        recode_sub(sub_path)


def main():
    parser = argparse.ArgumentParser(description="Process subtitles")
    subparsers = parser.add_subparsers(dest="subparser_name")

    parser_swap = subparsers.add_parser("swap", help="swap a sub up and bottom lines",)
    parser_swap.add_argument("-s", "--sub_path", required=True)
    parser_swap.add_argument("-n", "--new_path")
    parser_swap.add_argument("-m", "--media_path")
    parser_swap.add_argument("-l", "--sub_lang")

    parser_single = subparsers.add_parser(
        "single",
        help="process single sub (flip subtitle lines, rename with media name, copy to remote server",
    )
    parser_single.add_argument("-m", "--media_path", required=True)
    parser_single.add_argument("-s", "--sub_path", required=True)

    parser_multi = subparsers.add_parser(
        "multi",
        help='multiple subs in one folder (for TV), subtitle must contain episode number with format "S00E00"',
    )
    parser_multi.add_argument("-m", "--media_dir", required=True)
    parser_multi.add_argument("-s", "--sub_dir", required=True)

    parser_extract = subparsers.add_parser(
        "extract",
        help="Extract files from subdirectories follow pattern given or EXTRACT_PATTERN in config.ini",
    )
    parser_extract.add_argument("-i", "--input", required=True)
    parser_extract.add_argument("-o", "--output", required=True)
    parser_extract.add_argument("-p", "--pattern")

    parser_rename = subparsers.add_parser(
        "rename", help="Rename subtitle files in sequence"
    )
    parser_rename.add_argument("-i", "--input", required=True)
    parser_rename.add_argument("-s", "--season", required=True, type=int)
    parser_rename.add_argument("-e", "--init_episode", default=1)

    parser_rename = subparsers.add_parser(
        "rename_from", help="Rename subtitle files from name list"
    )
    parser_rename.add_argument("-i", "--input", required=True)
    parser_rename.add_argument("-n", "--name_file", required=True)

    parser_merge = subparsers.add_parser("merge", help="Merge subtitles")
    parser_merge.add_argument("-zh", "--zh_dir", required=True)
    parser_merge.add_argument("-en", "--en_dir", required=True)
    parser_merge.add_argument("-m", "--media_dir", required=True)

    parser_shift = subparsers.add_parser("shift", help="Shift subtitles")
    parser_shift.add_argument("-ms", "--ms", required=True, type=int)
    parser_shift.add_argument("-s", "--sub_dir", required=True)

    parser_shift = subparsers.add_parser("recode", help="Recode subtitles")
    parser_shift.add_argument("-s", "--sub_dir", required=True)

    args = parser.parse_args()

    if args.subparser_name == "single":
        ssh = get_ssh_client()
        single_sub_process(args.media_path, args.sub_path, ssh.open_sftp())
        ssh.close()
    elif args.subparser_name == "multi":
        ssh = get_ssh_client()
        multi_subs_process(args.media_dir, args.sub_dir, ssh.open_sftp())
        ssh.close()
    elif args.subparser_name == "extract":
        extract_files(args.input, args.output, args.pattern)
    elif args.subparser_name == "rename":
        rename_files(args.input, args.season, args.init_episode)
    elif args.subparser_name == "rename_from":
        rename_from_file(args.input, args.name_file)
    elif args.subparser_name == "merge":
        merge_multi_subs(args.zh_dir, args.en_dir)
        ssh = get_ssh_client()
        multi_subs_process(args.media_dir, args.zh_dir, ssh.open_sftp(), False)
    elif args.subparser_name == "shift":
        shift_subs(args.sub_dir, args.ms)
    elif args.subparser_name == "recode":
        recode_subs(args.sub_dir)
    elif args.subparser_name == "swap":
        sub = Sub(args.sub_path)
        sub.swap()
        if args.media_path:
            media = MediaFile(args.media_path)
            media.get_sub(sub=sub, sub_lang=args.sub_lang)
        else:
            sub.save(save_path=args.new_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
