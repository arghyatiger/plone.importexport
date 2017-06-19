import json
import pdb
# An Adapter to serialize a Dexterity object into a JSON object.
from plone.restapi.interfaces import ISerializeToJson
# An adapter to deserialize a JSON object into an object in Plone.
from plone.restapi.interfaces import IDeserializeFromJson
from Products.Five import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.component import queryMultiAdapter
from zExceptions import BadRequest
import zope
import UserDict
from plone.restapi.exceptions import DeserializationError
from zope.publisher.interfaces.browser import IBrowserRequest
from DateTime import DateTime
from random import randint
from urlparse import urlparse
import csv
import StringIO, cStringIO
import zipfile
import urllib2, base64

# TODO: in advanced tab, allow user to change this
EXCLUDED_ATTRIBUTES = ['member', 'parent', 'items', 'changeNote', '@id', 'UID', 'scales']

class InMemoryZip(object):
    def __init__(self):
        # Create the in-memory file-like object
        self.in_memory_zip = StringIO.StringIO()

    def append(self, filename_in_zip, file_contents):
        '''Appends a file with name filename_in_zip and contents of
        file_contents to the in-memory zip.'''
        # Get a handle to the in-memory zip in append mode
        zf = zipfile.ZipFile(self.in_memory_zip, "a", zipfile.ZIP_DEFLATED, False)

        # Write the file to the in-memory zip
        zf.writestr(filename_in_zip, file_contents)

        # Mark the files as having been created on Windows so that
        # Unix permissions are not inferred as 0000
        for zfile in zf.filelist:
            zfile.create_system = 0

        return self

    def read(self):
        '''Returns a string with the contents of the in-memory zip.'''
        self.in_memory_zip.seek(0)
        return self.in_memory_zip.read()

class ImportExportView(BrowserView):
    """Import/Export page."""

    template = ViewPageTemplateFile('importexport.pt')

    # del EXCLUDED_ATTRIBUTES from data
    def exclude_attributes(self,data):
        if isinstance(data,dict):
            for key in data.keys():
                if isinstance(data[key],dict):
                    self.exclude_attributes(data[key])
                elif isinstance(data[key],list):
                    # pdb.set_trace()
                    for index in range(len(data[key])):
                        self.exclude_attributes(data[key][index])
                if key in EXCLUDED_ATTRIBUTES:
                    del data[key]

    def serialize(self, obj, path_):
        # pdb.set_trace()

        serializer = queryMultiAdapter((obj, self.request), ISerializeToJson)
        if not serializer:
            return []
        data = serializer()

        # store paths of child object items
        if 'items' in data.keys():
            path = []
            for id_ in data['items']:
                path.append(urlparse(id_['@id']).path)

        # del EXCLUDED_ATTRIBUTES from data
        self.exclude_attributes(data)

        data['path'] = path_
        results = [data]
        for member in obj.objectValues():
            # TODO: defualt plone config @portal_type?
            if member.portal_type!="Plone Site":
                results += self.serialize(member,path[0])
                del path[0]
        return results

    # self==parent of obj, obj== working context, data=metadata for context
    def deserialize(self, obj, data):
        # pdb.set_trace()

        id_ = data.get('id', None)
        type_ = data.get('@type', None)
        title = data.get('title', None)

        if not type_:
            raise BadRequest("Property '@type' is required")


        # creating  random id
        if not id_:
            now = DateTime()
            new_id = '{}.{}.{}{:04d}'.format(
                type_.lower().replace(' ', '_'),
                now.strftime('%Y-%m-%d'),
                str(now.millis())[7:],
                randint(0, 9999))
        else:
            new_id = id_

        if not title:
            title = new_id

        # check if context exist
        if new_id not in obj.keys():
            print 'creating new object'
            # Create object
            try:
                ''' invokeFactory() is more generic, it can be used for any type of content, not just Dexterity content
                and it creates a new object at http://localhost:8080/self.context/new_id '''

                new_id = obj.invokeFactory(type_, new_id, title=title)
            except BadRequest as e:
                self.request.response.setStatus(400)
                return dict(error=dict(
                    type='DeserializationError',
                    message=str(e.message)))
            except ValueError as e:
                self.request.response.setStatus(400)
                return dict(error=dict(
                    type='DeserializationError',
                    message=str(e.message)))

        # restapi expects a string of JSON data
        data = json.dumps(data)
        # creating a spoof request with data embeded in BODY attribute, as expected by restapi
        request = UserDict.UserDict(BODY=data)
        # binding request to BrowserRequest
        zope.interface.directlyProvides(request, IBrowserRequest)

        # context must be the parent request object
        context = obj[new_id]

        deserializer = queryMultiAdapter((context, request), IDeserializeFromJson)
        if deserializer is None:
            self.request.response.setStatus(501)
            return dict(error=dict(
                message='Cannot deserialize type {}'.format(
                    obj.portal_type)))

        try:
            deserializer()
            self.request.response.setStatus(201)
            print 'deserializer works'
            # TODO: all error log should be returned to user
            return 'None'
        except DeserializationError as e:
            self.request.response.setStatus(400)
            return dict(error=dict(
                type='DeserializationError',
                message=str(e)))

    # return unique keys from list
    def getcsvheaders(self,data):
        header = []
        for dict_ in data:
            for key in dict_.keys():
                if key not in header:
                    header.append(key)

        return header

    def writejsontocsv(self,data_list):
        csv_output = cStringIO.StringIO()

        csv_headers =self.getcsvheaders(data_list)

        if not csv_headers:
            raise BadRequest("check json data, no keys found")

        try:
            '''The optional restval parameter specifies the value to be written if the dictionary is missing a key in fieldnames. If the dictionary passed to the writerow() method contains a key not found in fieldnames, the optional extrasaction parameter indicates what action to take. If it is set to 'raise' a ValueError is raised. If it is set to 'ignore', extra values in the dictionary are ignored.'''
            writer = csv.DictWriter(csv_output, fieldnames=csv_headers,restval='Field NA', extrasaction='raise', dialect='excel')
            writer.writeheader()
            for data in data_list:
                for key in data.keys():
                    if not data[key]:
                        data[key]="Null"
                    if isinstance(data[key],(dict,list)):

                        # store blob content and replace url with path
                        if isinstance(data[key],dict) and 'download' in data[key].keys():
                            # pdb.set_trace()

                            parse = urlparse(data[key]['download']).path.split('/')
                            id_ = parse[1]
                            file_path = '/'.join(parse[2:-2])

                            try:
                                if data[key]['content-type'].split('/')[0]=='image':
                                    file_data = self.context.restrictedTraverse(str(file_path)+'/image').data
                                else:
                                    file_data = self.context.restrictedTraverse(str(file_path)+'/file').data
                            except:
                                print 'Blob data fetching error'
                            else:
                                filename = data[key]['filename']
                                data[key]['download'] = id_+'/'+file_path+'/'+filename
                                self.zip.append(data[key]['download'],file_data)

                        data[key] = json.dumps(data[key])
                writer.writerow(data)
        except IOError as (errno, strerror):
                print("I/O error({0}): {1}".format(errno, strerror))

        data =  csv_output.getvalue()
        csv_output.close()

        return data

    def export(self):
        # pdb.set_trace()
        if self.request.method == 'POST':

            # get home_path of Plone sites
            url = self.request.URL
            id_ = urlparse(url).path.split('/')[1]
            home_path = '/' + id_

            # results is a list of dicts
            results = self.serialize(self.context, home_path)

            # create zip in memory
            self.zip = InMemoryZip()

            csv_output = self.writejsontocsv(results)
            self.zip.append(id_+'.csv',csv_output)

            # self.request.RESPONSE.setHeader(
            #     'content-type', 'application/csv; charset=utf-8')
            self.request.RESPONSE.setHeader('content-type', 'application/zip')
            cd = 'attachment; filename=%s.zip' % (id_)
            self.request.RESPONSE.setHeader('Content-Disposition', cd)

            return self.zip.read()

        return

    def getparentcontext(self,data):
        path_ = data['path'].split('/')

        obj = self.context

        # traversing to the desired folder
        for index in range(2,len(path_)-1):
            obj = obj[path_[index]]

        return obj

    def imports(self):
        # pdb.set_trace()
        if self.request.method == 'POST':


            # csv_file = '/home/shriyanshagro/Awesome_Stuff/Plone/zinstance/src/plone.importexport/src/plone/importexport/browser/export.csv'
            # json_data = self.readcsvasjson(csv_file)
            # TODO: implement a pipeline for converting CSV to JSON
            # TODO: implement mechanism for file upload
            data = {"path": "/Plone/GSoC17", "description": "Just GSoC stuff", "@type":"Folder",'title':"GSoC17"
            # "id": "newfolder"
            }

            if not data['path']:
                raise BadRequest("Property 'path' is required")

            # return parent of context
            parent_context = self.getparentcontext(data)

            # all import error will be logged back
            importerrors = self.deserialize(parent_context,data)

            self.request.RESPONSE.setHeader(
                'content-type', 'application/json; charset=utf-8')
            return json.dumps(importerrors)

        return

    def __call__(self):
        return self.template()
