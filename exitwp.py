#!/usr/bin/env python

from xml.etree.ElementTree import ElementTree, XMLTreeBuilder
import os
import codecs
from datetime import datetime
from glob import glob
import re
import sys
import yaml
from BeautifulSoup import BeautifulSoup
from urlparse import urlparse, urljoin
from urllib import urlretrieve
from html2text import html2text_file

'''
exitwp - Wordpress xml exports to Jekykll blog format conversion

Tested with Wordpress 3.3.1 and jekyll 0.11.2

'''
######################################################
# Configration
######################################################
config = yaml.load(file('config.yaml', 'r'))
wp_exports = config['wp_exports']
build_dir = config['build_dir']
download_images = config['download_images']
target_format = config['target_format']
taxonomy_filter = set(config['taxonomies']['filter'])
taxonomy_entry_filter = config['taxonomies']['entry_filter']
taxonomy_name_mapping = config['taxonomies']['name_mapping']
item_type_filter = set(config['item_type_filter'])
item_field_filter = config['item_field_filter']
date_fmt = config['date_format']


class ns_tracker_tree_builder(XMLTreeBuilder):
    def __init__(self):
        XMLTreeBuilder.__init__(self)
        self._parser.StartNamespaceDeclHandler = self._start_ns
        self.namespaces = {}

    def _start_ns(self, prefix, ns):
        self.namespaces[prefix] = '{' + ns + '}'


def html2fmt(html, target_format):
    #   html = html.replace("\n\n", '<br/><br/>')
    #   html = html.replace('<pre lang="xml">', '<pre lang="xml"><![CDATA[')
    #   html = html.replace('</pre>', ']]></pre>')
    if target_format == 'html':
        return process_html(html)
    else:
        return html2text_file(html, None)


def process_html(html):
    lines = html.splitlines()
    target_html = []
    # flagging if we are inside a <em> tag
    # this is usually a chunk of lyrics so no p tag.
    is_in_em = False
    for line in lines:
        if not line.strip():
            # skip empty lines
            if is_in_em:
                line += unicode('<br>')
            target_html.append(line)
        elif not line.startswith('<') and not is_in_em:
            if line.find('Lyric') < 0:
                # add p tag for hanging text except for multi-line em tag
                line = unicode('<p>') + line
                line += unicode('</p>')
            else:
                line += unicode('<br>')
                is_in_em = True
                line += unicode('<br>')

            target_html.append(line)
        elif line.startswith('<!--more-->'):
            # discard this more tag causes problem when deploy
            continue
        elif line.find('<em>') >= 0 or line.find('Lyric') >= 0:
            # flagging if detect em tag or text Lyric
            line += unicode('<br>')
            target_html.append(line)
            is_in_em = True
        elif line.find('</em>') >= 0:
            target_html.append(line)
            is_in_em = False
        elif is_in_em or line.find('</strong>') >= 0 or line.find('</b>') >= 0:
            # append <br> if it is lyric or at the end of strong tag line
            line += unicode('<br>')
            line = replace_resource_urls(line)
            target_html.append(line)
        else:
            line = replace_resource_urls(line)
            target_html.append(line)

    target_html = '\n'.join(target_html)
    return target_html

def replace_resource_urls(line):
    if line.find('http://') < 0:
        return line

    newline = unicode()
    if line.find('www.wzhang.org') >= 0:
       newline = line.replace('www.wzhang.org/wp-content/uploads','wzhang.org.s3.amazonaws.com/images')
    elif line.find('wzhang.org') >= 0:
       newline = line.replace('wzhang.org/wp-content/uploads','wzhang.org.s3.amazonaws.com/images')
    elif line.find('www.shaziyegeming.net') >= 0:
       newline = line.replace('www.shaziyegeming.net/wp-content/uploads','wzhang.org.s3.amazonaws.com/images')
    else:
        newline = line

    return newline

def parse_wp_xml(file):
    parser = ns_tracker_tree_builder()
    tree = ElementTree()
    print "reading: " + wpe
    root = tree.parse(file, parser)
    ns = parser.namespaces
    ns[''] = ''

    c = root.find('channel')

    def parse_header():
        return {
            "title": unicode(c.find('title').text),
            "link": unicode(c.find('link').text),
            "description": unicode(c.find('description').text)
        }

    def parse_items():
        export_items = []
        xml_items = c.findall('item')
        for i in xml_items:
            taxanomies = i.findall('category')
            export_taxanomies = {}
            for tax in taxanomies:
                if not "domain" in tax.attrib:
                    continue
                t_domain = unicode(tax.attrib['domain'])
                t_entry = unicode(tax.text)
                if (not (t_domain in taxonomy_filter) and
                       not (t_domain in taxonomy_entry_filter and
                       taxonomy_entry_filter[t_domain] == t_entry)):
                    if not t_domain in export_taxanomies:
                            export_taxanomies[t_domain] = []
                    export_taxanomies[t_domain].append(t_entry)

            def gi(q, unicode_wrap=True):
                namespace = ''
                tag = ''
                if q.find(':') > 0:
                    namespace, tag = q.split(':', 1)
                else:
                    tag = q
                try:
                    result = i.find(ns[namespace] + tag).text
                    print result
                except AttributeError:
                    result = "No Content Found"
                if unicode_wrap:
                    result = unicode(result)
                return result

            body = gi('content:encoded')

            img_srcs = []
            if body is not None:
                try:
                    soup = BeautifulSoup(body)
                    img_tags = soup.findAll('img')
                    for img in img_tags:
                        img_srcs.append(img['src'])
                except:
                    print "could not parse html: " + body
            #print img_srcs

            export_item = {
                'title': gi('title'),
                'date': gi('wp:post_date'),
                'slug': gi('wp:post_name'),
                'status': gi('wp:status'),
                'type': gi('wp:post_type'),
                'wp_id': gi('wp:post_id'),
                'parent': gi('wp:post_parent'),
                'taxanomies': export_taxanomies,
                'body': body,
                'img_srcs': img_srcs
                }

            export_items.append(export_item)

        return export_items

    return {
        'header': parse_header(),
        'items': parse_items(),
    }


def write_jekyll(data, target_format):

    sys.stdout.write("writing")
    item_uids = {}
    attachments = {}

    def get_blog_path(data, path_infix='jekyll'):
        name = data['header']['link']
        name = re.sub('^https?', '', name)
        name = re.sub('[^A-Za-z0-9_.-]', '', name)
        return os.path.normpath(build_dir + '/' + path_infix + '/' + name)

    blog_dir = get_blog_path(data)

    def get_full_dir(dir):
        full_dir = os.path.normpath(blog_dir + '/' + dir)
        if (not os.path.exists(full_dir)):
            os.makedirs(full_dir)
        return full_dir

    def open_file(file):
        f = codecs.open(file, 'w', encoding='utf-8')
        return f

    def get_item_uid(item, date_prefix=False, namespace=''):
        result = None
        if namespace not in item_uids:
            item_uids[namespace] = {}

        if item['wp_id'] in item_uids[namespace]:
            result = item_uids[namespace][item['wp_id']]
        else:
            uid = []
            if (date_prefix):
                dt = datetime.strptime(item['date'], date_fmt)
                uid.append(dt.strftime('%Y-%m-%d'))
                uid.append('-')
            s_title = item['slug']
            if s_title is None or s_title == '':
                s_title = item['title']
            if s_title is None or s_title == '':
                s_title = 'untitled'
            s_title = s_title.replace(' ', '_')
            s_title = re.sub('[^a-zA-Z0-9_-]', '', s_title)
            uid.append(s_title)
            fn = ''.join(uid)
            n = 1
            while fn in item_uids[namespace]:
                n = n + 1
                fn = ''.join(uid) + '_' + str(n)
                item_uids[namespace][i['wp_id']] = fn
            result = fn
        return result

    def get_item_path(item, dir=''):
        full_dir = get_full_dir(dir)
        filename_parts = [full_dir, '/']
        filename_parts.append(item['uid'])
        if item['type'] == 'page':
            if (not os.path.exists(''.join(filename_parts))):
                os.makedirs(''.join(filename_parts))
            filename_parts.append('/index')
        filename_parts.append('.')
        filename_parts.append(target_format)
        return ''.join(filename_parts)

    def get_attachment_path(src, dir, dir_prefix='a'):
        try:
            files = attachments[dir]
        except KeyError:
            attachments[dir] = files = {}

        try:
            filename = files[src]
        except KeyError:
            file_root, file_ext = os.path.splitext(os.path.basename(urlparse(src)[2]))
            file_infix = 1
            if file_root == '':
                file_root = '1'
            current_files = files.values()
            maybe_filename = file_root + file_ext
            while maybe_filename in current_files:
                maybe_filename = file_root + '-' + str(file_infix) + file_ext
                file_infix = file_infix + 1
            files[src] = filename = maybe_filename

        target_dir = os.path.normpath(blog_dir + '/' + dir_prefix + '/' + dir)
        target_file = os.path.normpath(target_dir + '/' + filename)

        if (not os.path.exists(target_dir)):
            os.makedirs(target_dir)

        #if src not in attachments[dir]:
        ##print target_name
        return target_file

    for i in data['items']:
        skip_item = False

        for field, value in item_field_filter.iteritems():
            if(i[field] == value):
                skip_item = True
                break

        if(skip_item):
            continue

        sys.stdout.write(".")
        sys.stdout.flush()
        out = None
        yaml_header = {
          'title': i['title'],
          'date': i['date'],
          'slug': i['slug'],
          'status': i['status'],
          'wordpress_id': i['wp_id'],
        }

        if i['type'] == 'post':
            i['uid'] = get_item_uid(i, date_prefix=True)
            fn = get_item_path(i, dir='_posts')
            out = open_file(fn)
            yaml_header['layout'] = 'post'
        elif i['type'] == 'page':
            i['uid'] = get_item_uid(i)
            # Chase down parent path, if any
            parentpath = ''
            item = i
            while item['parent'] != "0":
                item = next((parent for parent in data['items'] if parent['wp_id'] == item['parent']), None)
                if item:
                    parentpath = get_item_uid(item) + "/" + parentpath
                else:
                    break
            fn = get_item_path(i, parentpath)
            out = open_file(fn)
            yaml_header['layout'] = 'page'
        elif i['type'] in item_type_filter:
            pass
        else:
            print "Unknown item type :: " + i['type']

        if download_images:
            for img in i['img_srcs']:
                try:
                    urlretrieve(urljoin(data['header']['link'],
                                        img.decode('utf-8')),
                                        get_attachment_path(img, i['uid']))
                except:
                    print "\n unable to download " + urljoin(data['header']['link'], img.decode('utf-8'))

        if out is not None:
            def toyaml(data):
                return yaml.safe_dump(data, allow_unicode=True, default_flow_style=False).decode('utf-8')

            tax_out = {}
            for taxonomy in i['taxanomies']:
                for tvalue in i['taxanomies'][taxonomy]:
                    t_name = taxonomy_name_mapping.get(taxonomy, taxonomy)
                    if t_name not in tax_out:
                        tax_out[t_name] = []
                    if tvalue in tax_out[t_name]:
                        continue
                    tax_out[t_name].append(tvalue)

            out.write('---\n')
            if len(yaml_header) > 0:
                out.write(toyaml(yaml_header))
                out.write('comments: true\n')
            if len(tax_out) > 0:
                out.write(toyaml(tax_out))

            out.write('---\n\n')
            try:
                out.write(html2fmt(i['body'], target_format))
            except:
                print "\n Parse error on: " + i['title']

            out.close()
    print "\n"

wp_exports = glob(wp_exports + '/*.xml')
for wpe in wp_exports:
    data = parse_wp_xml(wpe)
    write_jekyll(data, target_format)

print 'done'
