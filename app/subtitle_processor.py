import os
import io
import re
import jieba
from pypinyin import lazy_pinyin, Style, pinyin
import logging
import tempfile

class SubtitleProcessor:
    def __init__(self):
        pass

    def process_text(self, text):
        text = text.replace("\n", "")
        text = text.replace("\r", "")
        text = text.replace("\t", "")
        text = text.strip()
        text = text.replace(" ", "")
        return text

    def segment(self, text):
        word_list = jieba.cut(text, use_paddle=False, cut_all=False)
        return word_list

    def pinyin_lize(self, word_list, sentStyle=True):
        pinyin_list = []
        for word in word_list:
            pinyin_list.append("".join(lazy_pinyin(word, style=Style.TONE, v_to_u=True, strict=False)))
        
        if sentStyle:
            pinyin_string = ' '.join(pinyin_list)
            final_result = pinyin_string.capitalize()
        else:
            pinyin_list = [pinyin.capitalize() for pinyin in pinyin_list]
            final_result = ' '.join(pinyin_list)
        
        return final_result

    def to_pinyin(self, text, sent_style=True):
        post_text = self.process_text(text)
        word_list = self.segment(post_text)
        pinyin_text = self.pinyin_lize(word_list, sentStyle=sent_style)
        return pinyin_text

    def segment_and_pinyin(self, text):
        words = jieba.lcut(text)
        pinyin_list = pinyin(words, style=Style.TONE3)
        pinyin_words = ' '.join(word[0] for word in pinyin_list)
        segmented_text = ' '.join(words)
        return pinyin_words, segmented_text

    def generate_pinyin_subtitle_file(self, filepath, destination):
        # Open and read the input SRT file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".srt") as temp_srt:
            with open(filepath, 'r', encoding='utf-8') as vtt_file:
                lines = vtt_file.readlines()

            new_lines = []

            # Process each line in the SRT file
            for line in lines:
                match = re.match(r'^\d+$\n', line)
                if match or '-->' in line or line.strip() == '':
                    new_lines.append(line)
                else:
                    # Get pinyin conversion using the refactored to_pinyin method
                    pinyin_output = self.to_pinyin(line.strip(), sent_style=True)
                    # Get segmented Chinese text
                    segmented_text = ' '.join(jieba.lcut(line.strip()))
                    new_lines.append(pinyin_output + '\n')
                    new_lines.append(segmented_text + '\n')
                    new_lines.append('\n')  # Optional: Add an empty line to separate segments neatly

            with open(destination, 'w', encoding='utf-8') as file:
                file.writelines(new_lines)