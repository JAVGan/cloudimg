from copy import deepcopy
from tempfile import NamedTemporaryFile
import unittest

from unittest.mock import MagicMock, patch

from cloudimg.aws import (
    AWSService, AWSPublishingMetadata, ClientError,
    SnapshotError, SnapshotTimeout, AWSDeleteMetadata, UploadProgress
)


class TestAWSPublishingMetadata(unittest.TestCase):

    def test_container_not_defined(self):
        """
        Test that container must be defined in the metadata.
        """
        self.assertRaises(AssertionError,
                          AWSPublishingMetadata,
                          image_path='/some/fake/path/to/image.raw',
                          image_name='fakeimagename')

    def test_default_snapshot_name(self):
        """
        Test that a snapshot name by default is derived from the image
        filename.
        """
        metadata = AWSPublishingMetadata(image_path='/somedir/some-image.raw',
                                         image_name='fakeimagename',
                                         container='abcdef')

        self.assertEqual(metadata.snapshot_name, 'some-image')

    def test_explicit_snapshot_name(self):
        """
        Test that a snapshot name can be provided explicitly.
        """
        metadata = AWSPublishingMetadata(image_path='/somedir/some-image.raw',
                                         image_name='fakeimagename',
                                         snapshot_name='mysnapshot',
                                         container='abcdef')

        self.assertEqual(metadata.snapshot_name, 'mysnapshot')


class TestAWSService(unittest.TestCase):

    def setUp(self):
        self.init_service()

    def tearDown(self):
        patch.stopall()

    def init_service(self, region='us-east-1', import_role=None):
        self.svc = AWSService('fakeaccessid', 'fakesecretkey',
                              region=region, import_role=import_role)

        self.md = AWSPublishingMetadata(
            image_path='/some/fake/path/to/image.raw',
            image_name='fakeimagename',
            container='fakecontainername'
        )

        ec2_client = self.svc.ec2.meta.client
        s3_client = self.svc.s3.meta.client

        # Mocked EC2/S3 methods
        self.mock_describe_images = \
            patch.object(ec2_client, 'describe_images').start()
        self.mock_describe_snapshots = \
            patch.object(ec2_client, 'describe_snapshots').start()
        self.mock_import_snapshot = \
            patch.object(ec2_client, 'import_snapshot').start()
        self.mock_describe_import_snapshot_tasks = \
            patch.object(ec2_client, 'describe_import_snapshot_tasks').start()
        self.mock_upload_fileobj = \
            patch.object(s3_client, 'upload_fileobj').start()
        self.mock_head_bucket = patch.object(s3_client, 'head_bucket').start()
        self.mock_upload_file = patch.object(s3_client, 'upload_file').start()
        self.mock_object = patch.object(self.svc.s3, 'Object').start()
        self.mock_bucket = patch.object(self.svc.s3, 'Bucket').start()
        self.mock_register_image = \
            patch.object(self.svc.ec2, 'register_image').start()

    def test_init_upload_service(self):
        upload_svc = UploadProgress("fake_container", "fake_object")
        assert upload_svc.container_name == "fake_container"
        assert upload_svc.object_name == "fake_object"
        assert upload_svc._size is None
        assert upload_svc._seen == 0
        assert upload_svc._last_log == 0
        assert upload_svc.determinate is False
        try:
            upload_svc.done
        except AssertionError as e:
            assert str(e) == "done unsupported for indeterminate uploads"

        with self.assertLogs(level='INFO') as log:
            expected_log = ("INFO:cloudimg.aws:Bytes uploaded "
                            "(fake_container/fake_object): 12")
            upload_svc.__call__(12)
            assert log.output[0] == expected_log

    def test_init_upload_service_filepath(self):
        prefix = "test_init_upload_service_filepath_"
        with NamedTemporaryFile(prefix=prefix, suffix=".xz") as tmpfile:
            # Write some testing data
            tmpfile.seek(1020)
            tmpfile.write(b"1234")
            tmpfile.flush()
            upload_svc = UploadProgress("fake_container",
                                        "fake_object",
                                        tmpfile.name)
        assert upload_svc.container_name == "fake_container"
        assert upload_svc.object_name == "fake_object"
        assert upload_svc._size == 1024
        assert upload_svc._seen == 0
        assert upload_svc._last_log == 0
        assert upload_svc.determinate is True
        assert upload_svc.done is False

        with self.assertLogs(level='INFO') as log:
            upload_svc.__call__(1024)
            assert log.output[0] == ("INFO:cloudimg.aws:Bytes uploaded "
                                     "(fake_container/fake_object): "
                                     "1024/1024 (100.00%)")

    def test_get_image_by_name(self):
        self.mock_describe_images.return_value = {
            'Images': [{
                'ImageId': 'abc123'
            }]
        }

        img = self.svc.get_image_by_name('img-name')
        self.assertNotEqual(img, None)
        self.assertEqual(img.id, 'abc123')

    def test_get_image_by_name_does_not_exist(self):
        self.mock_describe_images.return_value = {'Images': []}
        img = self.svc.get_image_by_name('img-name')
        self.assertEqual(img, None)

    def test_get_image_by_id(self):
        self.mock_describe_images.return_value = {
            'Images': [{
                'ImageId': 'abc123'
            }]
        }

        img = self.svc.get_image_by_id('abc123')
        self.assertNotEqual(img, None)
        self.assertEqual(img.id, 'abc123')

    def test_get_image_by_id_does_not_exist(self):
        self.mock_describe_images.return_value = {'Images': []}
        img = self.svc.get_image_by_id('abc123')
        self.assertEqual(img, None)

    def test_get_snapshot_by_name(self):
        self.mock_describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'abc123'
            }]
        }

        snap = self.svc.get_snapshot_by_name('snap-name')
        self.assertNotEqual(snap, None)
        self.assertEqual(snap.id, 'abc123')

    def test_get_snapshot_by_name_does_not_exist(self):
        self.mock_describe_snapshots.return_value = {'Snapshots': []}
        snap = self.svc.get_snapshot_by_name('snap-name')
        self.assertEqual(snap, None)

    def test_get_snapshot_by_id(self):
        self.mock_describe_snapshots.return_value = {
            'Snapshots': [{
                'SnapshotId': 'abc123'
            }]
        }

        snap = self.svc.get_snapshot_by_id('abc123')
        self.assertNotEqual(snap, None)
        self.assertEqual(snap.id, 'abc123')

    def test_get_snapshot_by_id_does_not_exist(self):
        self.mock_describe_snapshots.return_value = {'Snapshots': []}
        snap = self.svc.get_snapshot_by_id('abc123')
        self.assertEqual(snap, None)

    def test_get_object_by_name(self):
        obj = self.mock_object.return_value = MagicMock()
        result = self.svc.get_object_by_name(self.md.container, 'obj-name')
        self.mock_object.assert_called_once_with(self.md.container, 'obj-name')
        obj.load.assert_called_once_with()
        self.assertEqual(obj, result)

    def test_get_object_by_name_does_not_exist(self):
        error = ClientError({'Error': {'Code': '404'}}, 'test-operation')
        self.mock_object.return_value.load.side_effect = error
        obj = self.svc.get_object_by_name(self.md.container, 'obj-name')
        self.mock_object.assert_called_once_with(self.md.container, 'obj-name')
        self.assertEqual(obj, None)

    def test_get_object_by_name_bad_code(self):
        error = ClientError({'Error': {'Code': '505'}}, 'test-operation')
        self.mock_object.return_value.load.side_effect = error
        try:
            _ = self.svc.get_object_by_name(self.md.container, 'obj-name')
        except ClientError as e:
            assert str(e) == ("An error occurred (505) when calling "
                              "the test-operation operation: Unknown")
        self.mock_object.assert_called_once_with(self.md.container, 'obj-name')

    def test_get_container_by_name(self):
        container = self.mock_bucket.return_value = MagicMock()
        result = self.svc.get_container_by_name(self.md.container)
        self.mock_head_bucket.assert_called_once_with(Bucket=self.md.container)
        self.mock_bucket.assert_called_once_with(self.md.container)
        self.assertEqual(container, result)

    def test_get_container_by_name_does_not_exist(self):
        error = ClientError({'Error': {'Code': '404'}}, 'test-operation')
        self.mock_head_bucket.side_effect = error
        container = self.svc.get_container_by_name(self.md.container)
        self.mock_head_bucket.assert_called_once_with(Bucket=self.md.container)
        self.assertEqual(container, None)

    def test_get_container_by_name_bad_code(self):
        error = ClientError({'Error': {'Code': '505'}}, 'test-operation')
        self.mock_head_bucket.side_effect = error
        try:
            _ = self.svc.get_container_by_name(self.md.container)
        except ClientError as e:
            assert str(e) == ("An error occurred (505) when calling the "
                              "test-operation operation: Unknown")
        self.mock_head_bucket.assert_called_once_with(Bucket=self.md.container)

    def test_create_container_us_east_1(self):
        container = self.mock_bucket.return_value = MagicMock()
        result = self.svc.create_container(self.md.container, prop_delay=0)
        container.create.assert_called_once_with()
        self.mock_bucket.assert_called_once_with(self.md.container)
        self.assertEqual(container, result)

    def test_create_container_not_us_east_1(self):
        self.init_service(region='us-east-2')
        container = self.mock_bucket.return_value = MagicMock()
        result = self.svc.create_container(self.md.container, prop_delay=0)
        container.create.assert_called_once_with(
                CreateBucketConfiguration={'LocationConstraint': 'us-east-2'})
        self.mock_bucket.assert_called_once_with(self.md.container)
        self.assertEqual(container, result)

    @patch('cloudimg.aws.UploadProgress')
    @patch('cloudimg.aws.AWSService.get_container_by_name')
    @patch('cloudimg.aws.AWSService.create_container')
    def test_upload_to_container_create_container(self, mock_create, mock_get,
                                                  mock_callback):
        mock_get.return_value = None
        obj = self.mock_object.return_value = MagicMock()

        result = self.svc.upload_to_container(self.md.image_path,
                                              self.md.container,
                                              self.md.object_name)

        mock_create.assert_called_once_with(self.md.container)
        self.assertEqual(obj, result)

    @patch('cloudimg.aws.UploadProgress')
    def test_upload_to_container_local_image(self, mock_callback):
        obj = self.mock_object.return_value = MagicMock()

        result = self.svc.upload_to_container(self.md.image_path,
                                              self.md.container,
                                              self.md.object_name)

        self.assertEqual(self.mock_upload_file.call_count, 1)
        self.assertEqual(self.mock_upload_fileobj.call_count, 0)
        self.assertEqual(obj, result)

    @patch('cloudimg.aws.UploadProgress')
    def test_upload_to_container_local_image_xz(self, mock_callback):
        obj = self.mock_object.return_value = MagicMock()
        prefix = "test_upload_to_container_local_image_xz_"

        with NamedTemporaryFile(prefix=prefix, suffix=".xz") as tmpfile:
            # Write some testing data
            tmpfile.seek(1020)
            tmpfile.write(b"1234")
            tmpfile.flush()
            object_name = tmpfile.name.split("/")[-1]
            result = self.svc.upload_to_container(tmpfile.name,
                                                  self.md.container,
                                                  object_name)

        self.assertEqual(self.mock_upload_file.call_count, 0)
        self.assertEqual(self.mock_upload_fileobj.call_count, 1)
        self.assertEqual(obj, result)

    @patch('cloudimg.aws.UploadProgress')
    @patch('cloudimg.aws.requests')
    def test_upload_to_container_remote_image(self, mock_requests,
                                              mock_callback):
        obj = self.mock_object.return_value = MagicMock()
        self.md.image_path = 'http:///some.fake.url/to/image.raw'

        result = self.svc.upload_to_container(self.md.image_path,
                                              self.md.container,
                                              self.md.object_name)

        mock_requests.get.assert_called_once_with(self.md.image_path,
                                                  stream=True,
                                                  timeout=30)
        self.assertEqual(self.mock_upload_file.call_count, 0)
        self.assertEqual(self.mock_upload_fileobj.call_count, 1)
        self.assertEqual(obj, result)

    def test_share_image(self):
        accounts = ['account1', 'account2']
        groups = ['group1', 'group2']

        image = MagicMock()

        self.svc.share_image(image, accounts=accounts, groups=groups)
        image.modify_attribute.assert_called_once_with(LaunchPermission={
            'Add': [
                {'UserId': 'account1'},
                {'UserId': 'account2'},
                {'Group': 'group1'},
                {'Group': 'group2'},
            ]
        })

    def test_share_image_no_op(self):
        accounts = groups = []

        image = MagicMock()

        self.svc.share_image(image, accounts=accounts, groups=groups)
        image.modify_attribute.assert_not_called()

    def test_share_snapshot(self):
        accounts = ['account1', 'account2']

        snapshot = MagicMock()

        self.svc.share_snapshot(snapshot, "snapshot_name", accounts=accounts, )
        snapshot.modify_attribute.assert_called_once_with(
            Attribute="createVolumePermission",
            CreateVolumePermission={
                'Add': [
                    {'UserId': 'account1'},
                    {'UserId': 'account2'},
                ]
            })

    @patch('cloudimg.aws.AWSService.wait_for_import_snapshot_task')
    def test_import_snapshot(self, mock_wait):
        snapshot = mock_wait.return_value = MagicMock()
        obj = MagicMock()

        result = self.svc.import_snapshot(obj, self.md.snapshot_name)

        self.assertEqual(snapshot, result)
        self.assertEqual(self.mock_import_snapshot.call_count, 1)
        self.assertEqual(snapshot.create_tags.call_count, 1)
        self.assertFalse('RoleName' in self.mock_import_snapshot.call_args[1])

    @patch('cloudimg.aws.AWSService.wait_for_import_snapshot_task')
    def test_import_snapshot_role(self, mock_wait):
        self.init_service(import_role='fake-role')

        snapshot = mock_wait.return_value = MagicMock()
        obj = MagicMock()

        result = self.svc.import_snapshot(obj, self.md.snapshot_name)

        self.assertEqual(snapshot, result)
        self.assertEqual(self.mock_import_snapshot.call_count, 1)
        self.assertEqual(snapshot.create_tags.call_count, 1)

        kwargs = self.mock_import_snapshot.call_args[1]
        self.assertTrue('RoleName' in kwargs)
        self.assertEqual(kwargs['RoleName'], 'fake-role')

    def test_wait_for_import_snapshot_task(self):
        task = {
            'ImportTaskId': 'task-abc123',
            'SnapshotTaskDetail': {
                'Status': 'active'
            }
        }

        task_rsp = deepcopy(task)
        task_rsp['SnapshotTaskDetail']['SnapshotId'] = 'snap-abc123'
        task_rsp['SnapshotTaskDetail']['Status'] = 'completed'
        tasks_rsp = {'ImportSnapshotTasks': [task_rsp]}

        self.mock_describe_import_snapshot_tasks.return_value = tasks_rsp

        result = self.svc.wait_for_import_snapshot_task(task, interval=0)
        self.assertEqual(result.id, 'snap-abc123')

    def test_wait_for_import_snapshot_task_error(self):
        task = {
            'ImportTaskId': 'task-abc123',
            'SnapshotTaskDetail': {
                'Status': 'active'
            }
        }

        task_rsp = deepcopy(task)
        task_rsp['SnapshotTaskDetail']['Status'] = 'Error'
        tasks_rsp = {'ImportSnapshotTasks': [task_rsp]}

        self.mock_describe_import_snapshot_tasks.return_value = tasks_rsp

        self.assertRaises(SnapshotError,
                          self.svc.wait_for_import_snapshot_task,
                          task,
                          interval=0)

    def test_wait_for_import_snapshot_task_timeout(self):
        task = {
            'ImportTaskId': 'task-abc123',
            'SnapshotTaskDetail': {
                'Status': 'active'
            }
        }

        tasks_rsp = {'ImportSnapshotTasks': [task]}

        self.mock_describe_import_snapshot_tasks.return_value = tasks_rsp

        self.assertRaises(SnapshotTimeout,
                          self.svc.wait_for_import_snapshot_task,
                          task,
                          interval=0)

    @patch('cloudimg.aws.AWSService.upload_to_container')
    @patch('cloudimg.aws.AWSService.import_snapshot')
    @patch('cloudimg.aws.AWSService.register_image')
    @patch('cloudimg.aws.AWSService.share_image')
    @patch('cloudimg.aws.AWSService.get_image_by_tags')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    @patch('cloudimg.aws.AWSService.get_object_by_name')
    def test_publish_image_found_by_name(self,
                                         get_object_by_name,
                                         get_snapshot_by_name,
                                         get_image_by_name,
                                         get_image_by_tags,
                                         share_image,
                                         register_image,
                                         import_snapshot,
                                         upload_to_container):
        image = MagicMock()
        get_image_by_name.return_value = image
        published = self.svc.publish(self.md)
        self.assertEqual(image, published)

        share_image.assert_called_once_with(image, accounts=[], groups=[])

        get_image_by_tags.assert_not_called()
        get_snapshot_by_name.assert_not_called()
        get_object_by_name.assert_not_called()
        register_image.assert_not_called()
        import_snapshot.assert_not_called()
        upload_to_container.assert_not_called()

    @patch('cloudimg.aws.AWSService.upload_to_container')
    @patch('cloudimg.aws.AWSService.import_snapshot')
    @patch('cloudimg.aws.AWSService.register_image')
    @patch('cloudimg.aws.AWSService.share_image')
    @patch('cloudimg.aws.AWSService.get_image_by_tags')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    @patch('cloudimg.aws.AWSService.get_object_by_name')
    def test_publish_image_found_by_tags(self,
                                         get_object_by_name,
                                         get_snapshot_by_name,
                                         get_image_by_name,
                                         get_image_by_tags,
                                         share_image,
                                         register_image,
                                         import_snapshot,
                                         upload_to_container):
        image = MagicMock()
        get_image_by_name.return_value = None
        get_image_by_tags.return_value = image
        self.md.tags = {"tag": "tag"}
        published = self.svc.publish(self.md)
        self.assertEqual(image, published)

        share_image.assert_called_once_with(image, accounts=[], groups=[])
        get_image_by_name.assert_called_once_with(self.md.image_name)
        get_image_by_tags.assert_called_once_with(self.md.tags)

        get_snapshot_by_name.assert_not_called()
        get_object_by_name.assert_not_called()
        register_image.assert_not_called()
        import_snapshot.assert_not_called()
        upload_to_container.assert_not_called()

    @patch('cloudimg.aws.AWSService.upload_to_container')
    @patch('cloudimg.aws.AWSService.import_snapshot')
    @patch('cloudimg.aws.AWSService.register_image')
    @patch('cloudimg.aws.AWSService.share_image')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    @patch('cloudimg.aws.AWSService.get_object_by_name')
    def test_publish_snapshot_found(self,
                                    get_object_by_name,
                                    get_snapshot_by_name,
                                    get_image_by_name,
                                    share_image,
                                    register_image,
                                    import_snapshot,
                                    upload_to_container):
        snapshot = MagicMock()
        get_image_by_name.return_value = None
        get_snapshot_by_name.return_value = snapshot
        published = self.svc.publish(self.md)

        register_image.assert_called_once_with(snapshot, self.md)
        share_image.assert_called_once_with(published, accounts=[], groups=[])

        get_object_by_name.assert_not_called()
        import_snapshot.assert_not_called()
        upload_to_container.assert_not_called()

    @patch('cloudimg.aws.AWSService.upload_to_container')
    @patch('cloudimg.aws.AWSService.import_snapshot')
    @patch('cloudimg.aws.AWSService.register_image')
    @patch('cloudimg.aws.AWSService.share_image')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    @patch('cloudimg.aws.AWSService.get_object_by_name')
    def test_publish_object_found(self,
                                  get_object_by_name,
                                  get_snapshot_by_name,
                                  get_image_by_name,
                                  share_image,
                                  register_image,
                                  import_snapshot,
                                  upload_to_container):
        obj = MagicMock()
        get_image_by_name.return_value = None
        get_snapshot_by_name.return_value = None
        get_object_by_name.return_value = obj
        published = self.svc.publish(self.md)

        self.assertEqual(register_image.call_count, 1)
        share_image.assert_called_once_with(published, accounts=[], groups=[])
        import_snapshot.assert_called_once_with(obj, self.md.snapshot_name)

        upload_to_container.assert_not_called()

    @patch('cloudimg.aws.AWSService.upload_to_container')
    @patch('cloudimg.aws.AWSService.import_snapshot')
    @patch('cloudimg.aws.AWSService.register_image')
    @patch('cloudimg.aws.AWSService.share_image')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    @patch('cloudimg.aws.AWSService.get_object_by_name')
    def test_publish(self,
                     get_object_by_name,
                     get_snapshot_by_name,
                     get_image_by_name,
                     share_image,
                     register_image,
                     import_snapshot,
                     upload_to_container):
        get_image_by_name.return_value = None
        get_snapshot_by_name.return_value = None
        get_object_by_name.return_value = None
        published = self.svc.publish(self.md)

        share_image.assert_called_once_with(published, accounts=[], groups=[])
        self.assertEqual(register_image.call_count, 1)
        self.assertEqual(import_snapshot.call_count, 1)
        upload_to_container.assert_called_once_with(self.md.image_path,
                                                    self.md.container,
                                                    self.md.object_name)

    @patch('cloudimg.aws.AWSService.tag_image')
    def test_register_image_no_tags(self, tag_image):
        self.mock_register_image.return_value = "fakeimg"
        mock_snapshot = MagicMock()
        mock_snapshot.id = 'foo'
        block_device_mapping = [{
            'DeviceName': self.md.root_device_name,
            'Ebs': {
                'SnapshotId': mock_snapshot.id,
                'VolumeType': self.md.volume_type,
                'DeleteOnTermination': True,
            },
        }]

        res = self.svc.register_image(mock_snapshot, self.md)

        self.mock_register_image.assert_called_once_with(
            Name=self.md.image_name,
            Description=self.md.description,
            Architecture=self.md.arch,
            VirtualizationType=self.md.virt_type,
            RootDeviceName=self.md.root_device_name,
            BlockDeviceMappings=block_device_mapping,
            EnaSupport=self.md.ena_support,
            SriovNetSupport=self.md.sriov_net_support,
            BillingProducts=self.md.billing_products,
            BootMode=self.md.boot_mode
        )
        tag_image.assert_not_called()
        self.assertEqual(res, "fakeimg")

    @patch('cloudimg.aws.AWSService.tag_image')
    def test_register_image_tags(self, tag_image):
        self.md.tags = {"tag": "tag"}
        self.mock_register_image.return_value = "fakeimg"

        res = self.svc.register_image(MagicMock(), self.md)

        self.mock_register_image.assert_called_once()
        tag_image.assert_called_once_with("fakeimg", self.md.tags)
        self.assertEqual(res, "fakeimg")

    def test_deregister_image(self):
        image = MagicMock()
        out = self.svc.deregister_image(image)
        image.deregister.assert_called_once()
        assert out

    def test_delete_snapshot(self):
        snapshot = MagicMock()
        out = self.svc.delete_snapshot(snapshot)
        snapshot.delete.assert_called_once()
        assert out

    @patch('cloudimg.aws.AWSService.get_image_by_id')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_id')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    def test_delete_image_exists(self,
                                 get_snapshot_by_name,
                                 get_image_by_name,
                                 get_snapshot_by_id,
                                 get_image_by_id):
        """
        Tests basic scenario of image deletion when image_id is
        the only provided metadata and snapshot id are extrcted from image.
        """
        # setup testing data
        image_id = "fake_image_id"
        snapshot_id = "fake_snapshot_id"

        delete_meta = AWSDeleteMetadata(
            image_id=image_id
        )

        image = MagicMock()
        image.id = image_id
        image.block_device_mappings = [{"Ebs": {"SnapshotId": snapshot_id}}]

        snapshot = MagicMock()
        snapshot.id = snapshot_id

        get_image_by_id.return_value = image
        get_snapshot_by_id.return_value = snapshot

        # run delete
        deleted_image_id, deleted_snapshot_id = self.svc.delete(delete_meta)

        # check image related calls
        get_image_by_id.assert_called_once_with(image_id)
        get_image_by_name.assert_not_called()

        # check snapshot related calls
        get_snapshot_by_id.assert_called_once_with(snapshot_id)
        get_snapshot_by_name.assert_not_called()

        assert deleted_image_id == image_id
        assert deleted_snapshot_id == snapshot_id

    @patch('cloudimg.aws.AWSService.get_image_by_id')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_id')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    def test_delete_skip_snapshot(self,
                                  get_snapshot_by_name,
                                  get_image_by_name,
                                  get_snapshot_by_id,
                                  get_image_by_id):
        """
        Tests scenario when metadata for deletion includes skip_snapshot=True.
        In this case only image is deregistered and snapshot is kept.
        """
        # setup testing data
        image_id = "fake_image_id"
        snapshot_id = "fake_snapshot_id"

        delete_meta = AWSDeleteMetadata(
            image_id=image_id,
            skip_snapshot=True,
        )

        image = MagicMock()
        image.id = image_id
        image.block_device_mappings = [{"Ebs": {"SnapshotId": snapshot_id}}]

        snapshot = MagicMock()
        snapshot.id = snapshot_id

        get_image_by_id.return_value = image
        get_snapshot_by_id.return_value = snapshot

        # run delete
        deleted_image_id, deleted_snapshot_id = self.svc.delete(delete_meta)

        # check image related calls
        get_image_by_id.assert_called_once_with(image_id)
        get_image_by_name.assert_not_called()

        # check snapshot related calls
        get_snapshot_by_id.assert_called_once_with(snapshot_id)
        get_snapshot_by_name.assert_not_called()

        assert deleted_image_id == image_id
        assert deleted_snapshot_id is None

    @patch('cloudimg.aws.AWSService.get_image_by_id')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_id')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    def test_delete_image_missing(self,
                                  get_snapshot_by_name,
                                  get_image_by_name,
                                  get_snapshot_by_id,
                                  get_image_by_id):
        """
        Tests scenario when images is not present in AWS, but we at least try
        to find snapshot related to the image by provided metadata.
        """
        # setup testing data
        image_id = "fake_image_id"
        snapshot_id = "fake_snapshot_id"

        delete_meta = AWSDeleteMetadata(
            image_id=image_id,
            snapshot_id=snapshot_id,
        )

        snapshot = MagicMock()
        snapshot.id = snapshot_id

        get_image_by_id.return_value = None
        get_image_by_name.return_value = None
        get_snapshot_by_id.return_value = snapshot

        # run delete
        deleted_image_id, deleted_snapshot_id = self.svc.delete(delete_meta)

        # check image related calls
        get_image_by_id.assert_called_once_with(image_id)
        get_image_by_name.assert_called_once_with(None)

        # check snapshot related calls
        get_snapshot_by_id.assert_called_once_with(snapshot_id)
        get_snapshot_by_name.assert_not_called()

        assert deleted_image_id is None
        assert deleted_snapshot_id == snapshot_id

    @patch('cloudimg.aws.AWSService.get_image_by_id')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_id')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    def test_delete_snapshot_missing(self,
                                     get_snapshot_by_name,
                                     get_image_by_name,
                                     get_snapshot_by_id,
                                     get_image_by_id):
        """
        Tests scenario of image deletion when image exists but snapshot
        doesn't.
        """
        # setup testing data
        image_id = "fake_image_id"
        snapshot_id = "fake_snapshot_id"

        delete_meta = AWSDeleteMetadata(
            image_id=image_id
        )

        image = MagicMock()
        image.id = image_id
        image.block_device_mappings = [{"Ebs": {"SnapshotId": snapshot_id}}]

        snapshot = MagicMock()
        snapshot.id = snapshot_id

        get_image_by_id.return_value = image
        get_snapshot_by_id.return_value = None

        # run delete
        deleted_image_id, deleted_snapshot_id = self.svc.delete(delete_meta)

        # check image related calls
        get_image_by_id.assert_called_once_with(image_id)
        get_image_by_name.assert_not_called()

        # trying to search snapshot which is not found
        # and delete_snapshot is not called
        get_snapshot_by_id.assert_called_once_with(snapshot_id)
        get_snapshot_by_name.assert_not_called()

        assert deleted_image_id == image_id
        assert deleted_snapshot_id is None

    @patch('cloudimg.aws.AWSService.get_image_by_id')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_id')
    @patch('cloudimg.aws.AWSService.get_image_by_name')
    @patch('cloudimg.aws.AWSService.get_snapshot_by_name')
    def test_delete_snapshot_not_referenced_in_image(self,
                                                     get_snapshot_by_name,
                                                     get_image_by_name,
                                                     get_snapshot_by_id,
                                                     get_image_by_id):
        """
        Tests scenario of image deletion when image doesn't
        reference related snapshot.
        """
        # setup testing data
        image_id = "fake_image_id"

        delete_meta = AWSDeleteMetadata(
            image_id=image_id
        )

        image = MagicMock()
        image.id = image_id
        # set empty list to block_device_mappings to simulate
        # missing reference of snapshot in image
        image.block_device_mappings = []

        get_image_by_id.return_value = image
        get_snapshot_by_id.return_value = None

        # run delete
        deleted_image_id, deleted_snapshot_id = self.svc.delete(delete_meta)

        # check image related calls
        get_image_by_id.assert_called_once_with(image_id)
        get_image_by_name.assert_not_called()

        # check snapshot realted calls
        get_snapshot_by_id.assert_called_once_with(None)
        get_snapshot_by_name.assert_not_called()

        assert deleted_image_id == image_id
        assert deleted_snapshot_id is None

    @patch('cloudimg.aws.AWSService.get_image_by_filters')
    def test_get_image_by_tags(self, get_image_by_filters):
        get_image_by_filters.return_value = "fake_image"
        tags = {
            "tags": "tag"
        }
        filters_call = [
            {
                "Name": "tag:tags",
                "Values": ["tag"]
            }
        ]
        image = self.svc.get_image_by_tags(tags)
        assert image == "fake_image"
        get_image_by_filters.assert_called_once_with(filters_call)

    def test_tag_image(self):
        class fake_image_class:
            name: str = "fake_image"
            dry_run: str
            tags: list

            def create_tags(self, **args):
                self.dry_run = args.pop("DryRun")
                self.tags = args.pop("Tags")
                return self
        tags = {
            "tags": "tag"
        }
        tag_resp = [
            {
                "Key": "tags",
                "Value": "tag"
            }
        ]
        fake_image = fake_image_class()
        image = self.svc.tag_image(fake_image, tags)
        assert image.dry_run is False
        assert image.tags == tag_resp


if __name__ == '__main__':
    unittest.main()
