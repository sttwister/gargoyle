"""
:copyright: (c) 2010 DISQUS.
:license: Apache License 2.0, see LICENSE for more details.
"""

import datetime
import sys

from django.conf import settings
from django.contrib.auth.models import User, AnonymousUser
from django.core.cache import cache
from django.core.management.base import CommandError
from django.core.management import call_command
from django.http import HttpRequest, Http404, HttpResponse
from django.utils import simplejson
from django.test import TestCase
from django.template import Context, Template, TemplateSyntaxError

import gargoyle
from gargoyle.builtins import IPAddressConditionSet, UserConditionSet, HostConditionSet
from gargoyle.decorators import switch_is_active
from gargoyle.helpers import MockRequest
from gargoyle.models import Switch, SELECTIVE, DISABLED, GLOBAL, INHERIT
from gargoyle.management.commands.add_switch import Command as AddSwitchCmd
from gargoyle.management.commands.remove_switch import (
    Command as RemoveSwitchCmd
)
from gargoyle.manager import SwitchManager
from gargoyle.testutils import switches

import socket


class APITest(TestCase):
    urls = 'tests.urls'

    def setUp(self):
        self.user = User.objects.create(username='foo', email='foo@example.com')
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True, auto_create=True)
        self.gargoyle.register(UserConditionSet(User))
        self.gargoyle.register(IPAddressConditionSet())
        self.internal_ips = settings.INTERNAL_IPS

    def tearDown(self):
        settings.INTERNAL_IPS = self.internal_ips

    def test_builtin_registration(self):
        self.assertTrue('gargoyle.builtins.UserConditionSet(auth.user)' in self.gargoyle._registry)
        self.assertTrue('gargoyle.builtins.IPAddressConditionSet' in self.gargoyle._registry)
        self.assertEquals(len(list(self.gargoyle.get_condition_sets())), 2, self.gargoyle)

    def test_user(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        # we need a better API for this (model dict isnt cutting it)
        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )

        user = User(pk=5)
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=8771)
        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='is_staff',
            condition='1',
        )

        user = User(pk=8771, is_staff=True)
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=8771, is_superuser=True)
        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='is_superuser',
            condition='1',
        )

        user = User(pk=8771, is_superuser=True)
        self.assertTrue(self.gargoyle.is_active('test', user))

        # test with mock request
        self.assertTrue(self.gargoyle.is_active('test', self.gargoyle.as_request(user=user)))

        # test date joined condition
        user = User(pk=8771)
        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='date_joined',
            condition='2011-07-01',
        )

        user = User(pk=8771, date_joined=datetime.datetime(2011, 07, 02))
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=8771, date_joined=datetime.datetime(2012, 07, 02))
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=8771, date_joined=datetime.datetime(2011, 06, 02))
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=8771, date_joined=datetime.datetime(2011, 07, 01))
        self.assertTrue(self.gargoyle.is_active('test', user))

        switch.clear_conditions(condition_set=condition_set)
        switch.add_condition(
            condition_set=condition_set,
            field_name='email',
            condition='bob@example.com',
        )

        user = User(pk=8771, email="bob@example.com")
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=8771, email="bob2@example.com")
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=8771)
        self.assertFalse(self.gargoyle.is_active('test', user))

    def test_exclusions(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='is_staff',
            condition='1',
        )
        switch.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='foo',
        )
        switch.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bar',
            exclude=True
        )

        user = User(pk=0, username='foo', is_staff=False)
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='foo', is_staff=True)
        self.assertTrue(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='bar', is_staff=False)
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='bar', is_staff=True)
        self.assertFalse(self.gargoyle.is_active('test', user))

    def test_only_exclusions(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bar',
            exclude=True
        )

        user = User(pk=0, username='foo', is_staff=False)
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='foo', is_staff=True)
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='bar', is_staff=False)
        self.assertFalse(self.gargoyle.is_active('test', user))

        user = User(pk=0, username='bar', is_staff=True)
        self.assertFalse(self.gargoyle.is_active('test', user))

    def test_decorator_for_user(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=DISABLED,
        )
        switch = self.gargoyle['test']

        @switch_is_active('test')
        def test(request):
            return True

        request = HttpRequest()
        request.user = self.user

        self.assertRaises(Http404, test, request)

        switch.status = SELECTIVE
        switch.save()

        self.assertRaises(Http404, test, request)

        switch.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='foo',
        )

        self.assertTrue(test(request))

    def test_decorator_for_ip_address(self):
        condition_set = 'gargoyle.builtins.IPAddressConditionSet'

        switch = Switch.objects.create(
            key='test',
            status=DISABLED,
        )
        switch = self.gargoyle['test']

        @switch_is_active('test')
        def test(request):
            return True

        request = HttpRequest()
        request.META['REMOTE_ADDR'] = '192.168.1.1'

        self.assertRaises(Http404, test, request)

        switch.status = SELECTIVE
        switch.save()

        switch.add_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.1',
        )

        self.assertTrue(test(request))

        # add in a second condition, so that removing the first one won't kick
        # in the "no conditions returns is_active True for selective switches"
        switch.add_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.2',
        )

        switch.remove_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.1',
        )

        self.assertRaises(Http404, test, request)

        switch.add_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.1',
        )

        self.assertTrue(test(request))

        switch.clear_conditions(
            condition_set=condition_set,
            field_name='ip_address',
        )

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='50-100',
        )

        self.assertTrue(test(request))

        switch.clear_conditions(
            condition_set=condition_set,
        )

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )

        self.assertRaises(Http404, test, request)

    def test_decorator_with_redirect(self):
        Switch.objects.create(
            key='test',
            status=DISABLED,
        )

        request = HttpRequest()
        request.user = self.user

        @switch_is_active('test', redirect_to='/foo')
        def test(request):
            return HttpResponse()

        response = test(request)
        self.assertTrue(response.status_code, 302)
        self.assertTrue('Location' in response)
        self.assertTrue(response['Location'], '/foo')

        @switch_is_active('test', redirect_to='gargoyle_test_foo')
        def test2(request):
            return HttpResponse()

        response = test2(request)
        self.assertTrue(response.status_code, 302)
        self.assertTrue('Location' in response)
        self.assertTrue(response['Location'], '')

    def test_global(self):
        switch = Switch.objects.create(
            key='test',
            status=DISABLED,
        )
        switch = self.gargoyle['test']

        self.assertFalse(self.gargoyle.is_active('test'))
        self.assertFalse(self.gargoyle.is_active('test', self.user))

        switch.status = GLOBAL
        switch.save()

        self.assertTrue(self.gargoyle.is_active('test'))
        self.assertTrue(self.gargoyle.is_active('test', self.user))

    def test_disable(self):
        switch = Switch.objects.create(key='test')

        switch = self.gargoyle['test']

        switch.status = DISABLED
        switch.save()

        self.assertFalse(self.gargoyle.is_active('test'))

        self.assertFalse(self.gargoyle.is_active('test', self.user))

    def test_deletion(self):
        switch = Switch.objects.create(key='test')

        switch = self.gargoyle['test']

        self.assertTrue('test' in self.gargoyle)

        switch.delete()

        self.assertFalse('test' in self.gargoyle)

    def test_expiration(self):
        switch = Switch.objects.create(key='test')

        switch = self.gargoyle['test']

        switch.status = DISABLED
        switch.save()

        self.assertFalse(self.gargoyle.is_active('test'))

        Switch.objects.filter(key='test').update(value={}, status=GLOBAL)

        # cache shouldn't have expired
        self.assertFalse(self.gargoyle.is_active('test'))

        # lookup cache_key in a modeldict 1.2/1.4 compatible way
        if hasattr(self.gargoyle, 'remote_cache_key'):
            cache_key = self.gargoyle.remote_cache_key
        else:
            cache_key = self.gargoyle.cache_key
        # in memory cache shouldnt have expired
        cache.delete(cache_key)
        self.assertFalse(self.gargoyle.is_active('test'))
        switch.status, switch.value = GLOBAL, {}
        # Ensure post save gets sent
        self.gargoyle._post_save(sender=None, instance=switch, created=False)

        # any request should expire the in memory cache
        self.client.get('/')

        self.assertTrue(self.gargoyle.is_active('test'))

    def test_anonymous_user(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(key='test')

        switch = self.gargoyle['test']

        switch.status = SELECTIVE
        switch.save()

        user = AnonymousUser()

        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='1-10',
        )

        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.clear_conditions(
            condition_set=condition_set,
        )

        self.assertFalse(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='is_anonymous',
            condition='1',
        )

        self.assertTrue(self.gargoyle.is_active('test', user))

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='1-10',
        )

        self.assertTrue(self.gargoyle.is_active('test', user))

    def test_ip_address_internal_ips(self):
        condition_set = 'gargoyle.builtins.IPAddressConditionSet'

        Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        request = HttpRequest()
        request.META['REMOTE_ADDR'] = '192.168.1.1'

        self.assertFalse(self.gargoyle.is_active('test', request))

        switch.add_condition(
            condition_set=condition_set,
            field_name='internal_ip',
            condition='1',
        )

        settings.INTERNAL_IPS = ['192.168.1.1']

        self.assertTrue(self.gargoyle.is_active('test', request))

        settings.INTERNAL_IPS = []

        self.assertFalse(self.gargoyle.is_active('test', request))

    def test_ip_address(self):
        condition_set = 'gargoyle.builtins.IPAddressConditionSet'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        request = HttpRequest()
        request.META['REMOTE_ADDR'] = '192.168.1.1'

        self.assertFalse(self.gargoyle.is_active('test', request))

        switch.add_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.1',
        )

        self.assertTrue(self.gargoyle.is_active('test', request))

        switch.clear_conditions(
            condition_set=condition_set,
        )
        switch.add_condition(
            condition_set=condition_set,
            field_name='ip_address',
            condition='127.0.0.1',
        )

        self.assertFalse(self.gargoyle.is_active('test', request))

        switch.clear_conditions(
            condition_set=condition_set,
        )

        self.assertFalse(self.gargoyle.is_active('test', request))

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='50-100',
        )

        self.assertTrue(self.gargoyle.is_active('test', request))

        # test with mock request
        self.assertTrue(self.gargoyle.is_active('test', self.gargoyle.as_request(ip_address='192.168.1.1')))

        switch.clear_conditions(
            condition_set=condition_set,
        )
        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )
        self.assertFalse(self.gargoyle.is_active('test', request))

        self.assertTrue(self.gargoyle.is_active('test', self.gargoyle.as_request(ip_address='::1')))

        switch.clear_conditions(
            condition_set=condition_set,
        )
        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )
        self.assertFalse(self.gargoyle.is_active('test', request))

    def test_to_dict(self):
        condition_set = 'gargoyle.builtins.IPAddressConditionSet'

        switch = Switch.objects.create(
            label='my switch',
            description='foo bar baz',
            key='test',
            status=SELECTIVE,
        )

        switch.add_condition(
            manager=self.gargoyle,
            condition_set=condition_set,
            field_name='ip_address',
            condition='192.168.1.1',
        )

        result = switch.to_dict(self.gargoyle)

        self.assertTrue('label' in result)
        self.assertEquals(result['label'], 'my switch')

        self.assertTrue('status' in result)
        self.assertEquals(result['status'], SELECTIVE)

        self.assertTrue('description' in result)
        self.assertEquals(result['description'], 'foo bar baz')

        self.assertTrue('key' in result)
        self.assertEquals(result['key'], 'test')

        self.assertTrue('conditions' in result)
        self.assertEquals(len(result['conditions']), 1)

        condition = result['conditions'][0]
        self.assertTrue('id' in condition)
        self.assertEquals(condition['id'], condition_set)
        self.assertTrue('label' in condition)
        self.assertEquals(condition['label'], 'IP Address')
        self.assertTrue('conditions' in condition)
        self.assertEquals(len(condition['conditions']), 1)

        inner_condition = condition['conditions'][0]
        self.assertEquals(len(inner_condition), 4)
        self.assertTrue(inner_condition[0], 'ip_address')
        self.assertTrue(inner_condition[1], '192.168.1.1')
        self.assertTrue(inner_condition[2], '192.168.1.1')
        self.assertFalse(inner_condition[3])

    def test_remove_condition(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        user5 = User(pk=5, email='5@example.com')

        # inactive if selective with no conditions
        self.assertFalse(self.gargoyle.is_active('test', user5))

        user8771 = User(pk=8771, email='8771@example.com', is_superuser=True)
        switch.add_condition(
            condition_set=condition_set,
            field_name='is_superuser',
            condition='1',
        )
        self.assertTrue(self.gargoyle.is_active('test', user8771))
        # No longer is_active for user5 as we have other conditions
        self.assertFalse(self.gargoyle.is_active('test', user5))

        switch.remove_condition(
            condition_set=condition_set,
            field_name='is_superuser',
            condition='1',
        )

        # back to inactive for everyone with no conditions
        self.assertFalse(self.gargoyle.is_active('test', user5))
        self.assertFalse(self.gargoyle.is_active('test', user8771))

    def test_switch_defaults(self):
        """Test that defaults pulled from GARGOYLE_SWITCH_DEFAULTS.

        Requires SwitchManager to use auto_create.

        """
        self.assertTrue(self.gargoyle.is_active('active_by_default'))
        self.assertFalse(self.gargoyle.is_active('inactive_by_default'))
        self.assertEquals(
            self.gargoyle['inactive_by_default'].label,
            'Default Inactive',
        )
        self.assertEquals(
            self.gargoyle['active_by_default'].label,
            'Default Active',
        )
        active_by_default = self.gargoyle['active_by_default']
        active_by_default.status = DISABLED
        active_by_default.save()
        self.assertFalse(self.gargoyle.is_active('active_by_default'))

    def test_invalid_condition(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        user5 = User(pk=5, email='5@example.com')

        # inactive if selective with no conditions
        self.assertFalse(self.gargoyle.is_active('test', user5))

        user8771 = User(pk=8771, email='8771@example.com', is_superuser=True)
        switch.add_condition(
            condition_set=condition_set,
            field_name='foo',
            condition='1',
        )
        self.assertFalse(self.gargoyle.is_active('test', user8771))

    def test_inheritance(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        # we need a better API for this (model dict isnt cutting it)
        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )

        switch = Switch.objects.create(
            key='test:child',
            status=INHERIT,
        )
        switch = self.gargoyle['test']

        user = User(pk=5)
        self.assertTrue(self.gargoyle.is_active('test:child', user))

        user = User(pk=8771)
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        switch = self.gargoyle['test']
        switch.status = DISABLED

        user = User(pk=5)
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        user = User(pk=8771)
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        switch = self.gargoyle['test']
        switch.status = GLOBAL

        user = User(pk=5)
        self.assertTrue(self.gargoyle.is_active('test:child', user))

        user = User(pk=8771)
        self.assertTrue(self.gargoyle.is_active('test:child', user))

    def test_parent_override_child_state(self):
        Switch.objects.create(
            key='test',
            status=DISABLED,
        )

        Switch.objects.create(
            key='test:child',
            status=GLOBAL,
        )

        self.assertFalse(self.gargoyle.is_active('test:child'))

    def test_child_state_is_used(self):
        Switch.objects.create(
            key='test',
            status=GLOBAL,
        )

        Switch.objects.create(
            key='test:child',
            status=DISABLED,
        )

        self.assertFalse(self.gargoyle.is_active('test:child'))

    def test_parent_override_child_condition(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )

        parent = self.gargoyle['test']

        parent.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bob',
        )

        Switch.objects.create(
            key='test:child',
            status=GLOBAL,
        )

        user = User(username='bob')
        self.assertTrue(self.gargoyle.is_active('test:child', user))

        user = User(username='joe')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        self.assertFalse(self.gargoyle.is_active('test:child'))

    def test_child_condition_differing_than_parent_loses(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )

        parent = self.gargoyle['test']

        parent.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bob',
        )

        Switch.objects.create(
            key='test:child',
            status=SELECTIVE,
        )

        child = self.gargoyle['test:child']

        child.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='joe',
        )

        user = User(username='bob')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        user = User(username='joe')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        user = User(username='john')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        self.assertFalse(self.gargoyle.is_active('test:child'))

    def test_child_condition_including_parent_wins(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )

        parent = self.gargoyle['test']

        parent.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bob',
        )

        Switch.objects.create(
            key='test:child',
            status=SELECTIVE,
        )

        child = self.gargoyle['test:child']

        child.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='bob',
        )
        child.add_condition(
            condition_set=condition_set,
            field_name='username',
            condition='joe',
        )

        user = User(username='bob')
        self.assertTrue(self.gargoyle.is_active('test:child', user))

        user = User(username='joe')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        user = User(username='john')
        self.assertFalse(self.gargoyle.is_active('test:child', user))

        self.assertFalse(self.gargoyle.is_active('test:child'))


class ConstantTest(TestCase):
    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True)

    def test_disabled(self):
        self.assertTrue(hasattr(self.gargoyle, 'DISABLED'))
        self.assertEquals(self.gargoyle.DISABLED, 1)

    def test_selective(self):
        self.assertTrue(hasattr(self.gargoyle, 'SELECTIVE'))
        self.assertEquals(self.gargoyle.SELECTIVE, 2)

    def test_global(self):
        self.assertTrue(hasattr(self.gargoyle, 'GLOBAL'))
        self.assertEquals(self.gargoyle.GLOBAL, 3)

    def test_include(self):
        self.assertTrue(hasattr(self.gargoyle, 'INCLUDE'))
        self.assertEquals(self.gargoyle.INCLUDE, 'i')

    def test_exclude(self):
        self.assertTrue(hasattr(self.gargoyle, 'EXCLUDE'))
        self.assertEquals(self.gargoyle.EXCLUDE, 'e')


class MockRequestTest(TestCase):
    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True)

    def test_empty_attrs(self):
        req = MockRequest()
        self.assertEquals(req.META['REMOTE_ADDR'], None)
        self.assertEquals(req.user.__class__, AnonymousUser)

    def test_ip(self):
        req = MockRequest(ip_address='127.0.0.1')
        self.assertEquals(req.META['REMOTE_ADDR'], '127.0.0.1')
        self.assertEquals(req.user.__class__, AnonymousUser)

    def test_user(self):
        user = User.objects.create(username='foo', email='foo@example.com')
        req = MockRequest(user=user)
        self.assertEquals(req.META['REMOTE_ADDR'], None)
        self.assertEquals(req.user, user)

    def test_as_request(self):
        user = User.objects.create(username='foo', email='foo@example.com')

        req = self.gargoyle.as_request(user=user, ip_address='127.0.0.1')

        self.assertEquals(req.META['REMOTE_ADDR'], '127.0.0.1')
        self.assertEquals(req.user, user)


class TemplateTagTest(TestCase):
    urls = 'tests.urls'

    def setUp(self):
        self.user = User.objects.create(username='foo', email='foo@example.com')
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True)
        self.gargoyle.register(UserConditionSet(User))

    def test_simple(self):
        Switch.objects.create(
            key='test',
            status=GLOBAL,
        )

        template = Template("""
            {% load gargoyle_tags %}
            {% ifswitch test %}
            hello world!
            {% endifswitch %}
        """)
        rendered = template.render(Context())

        self.assertTrue('hello world!' in rendered)

    def test_else(self):
        Switch.objects.create(
            key='test',
            status=DISABLED,
        )

        template = Template("""
            {% load gargoyle_tags %}
            {% ifswitch test %}
            hello world!
            {% else %}
            foo bar baz
            {% endifswitch %}
        """)
        rendered = template.render(Context())

        self.assertTrue('foo bar baz' in rendered)
        self.assertFalse('hello world!' in rendered)

    def test_with_request(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )

        request = HttpRequest()
        request.user = self.user

        template = Template("""
            {% load gargoyle_tags %}
            {% ifswitch test %}
            hello world!
            {% else %}
            foo bar baz
            {% endifswitch %}
        """)
        rendered = template.render(Context({'request': request}))

        self.assertFalse('foo bar baz' in rendered)
        self.assertTrue('hello world!' in rendered)

    def test_missing_name(self):
        self.assertRaises(TemplateSyntaxError, Template, """
            {% load gargoyle_tags %}
            {% ifswitch %}
            hello world!
            {% endifswitch %}
        """)

    def test_with_custom_objects(self):
        condition_set = 'gargoyle.builtins.UserConditionSet(auth.user)'

        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        switch.add_condition(
            condition_set=condition_set,
            field_name='percent',
            condition='0-50',
        )

        request = HttpRequest()
        request.user = self.user

        # Pass in request.user explicitly.
        template = Template("""
            {% load gargoyle_tags %}
            {% ifswitch test request.user %}
            hello world!
            {% else %}
            foo bar baz
            {% endifswitch %}
        """)
        rendered = template.render(Context({'request': request}))

        self.assertFalse('foo bar baz' in rendered)
        self.assertTrue('hello world!' in rendered)


class HostConditionSetTest(TestCase):
    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True, auto_create=True)
        self.gargoyle.register(HostConditionSet())

    def test_simple(self):
        condition_set = 'gargoyle.builtins.HostConditionSet'

        # we need a better API for this (model dict isnt cutting it)
        switch = Switch.objects.create(
            key='test',
            status=SELECTIVE,
        )
        switch = self.gargoyle['test']

        self.assertFalse(self.gargoyle.is_active('test'))

        switch.add_condition(
            condition_set=condition_set,
            field_name='hostname',
            condition=socket.gethostname(),
        )

        self.assertTrue(self.gargoyle.is_active('test'))


class SwitchContextManagerTest(TestCase):
    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True, auto_create=True)

    def test_as_decorator(self):
        switch = self.gargoyle['test']
        switch.status = DISABLED

        @switches(self.gargoyle, test=True)
        def test():
            return self.gargoyle.is_active('test')

        self.assertTrue(test())
        self.assertEquals(self.gargoyle['test'].status, DISABLED)

        switch.status = GLOBAL
        switch.save()

        @switches(self.gargoyle, test=False)
        def test2():
            return self.gargoyle.is_active('test')

        self.assertFalse(test2())
        self.assertEquals(self.gargoyle['test'].status, GLOBAL)

    def test_context_manager(self):
        switch = self.gargoyle['test']
        switch.status = DISABLED

        with switches(self.gargoyle, test=True):
            self.assertTrue(self.gargoyle.is_active('test'))

        self.assertEquals(self.gargoyle['test'].status, DISABLED)

        switch.status = GLOBAL
        switch.save()

        with switches(self.gargoyle, test=False):
            self.assertFalse(self.gargoyle.is_active('test'))

        self.assertEquals(self.gargoyle['test'].status, GLOBAL)


class CommandAddSwitchTestCase(TestCase):

    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True, auto_create=True)

    def test_requires_single_arg(self):
        too_few_too_many = [
            [],
            ['one', 'two'],
        ]
        for args in too_few_too_many:
            command = AddSwitchCmd()

            self.assertRaises(CommandError, command.handle, *args)

    def test_add_switch_default_status(self):
        self.assertFalse('switch_default' in self.gargoyle)

        call_command('add_switch', 'switch_default')

        self.assertTrue('switch_default' in self.gargoyle)
        self.assertEqual(GLOBAL, self.gargoyle['switch_default'].status)

    def test_add_switch_with_status(self):
        self.assertFalse('switch_disabled' in self.gargoyle)

        call_command('add_switch', 'switch_disabled', status=DISABLED)

        self.assertTrue('switch_disabled' in self.gargoyle)
        self.assertEqual(DISABLED, self.gargoyle['switch_disabled'].status)

    def test_update_switch_status_disabled(self):
        Switch.objects.create(key='test', status=GLOBAL)
        self.assertEqual(GLOBAL, self.gargoyle['test'].status)

        call_command('add_switch', 'test', status=DISABLED)

        self.assertEqual(DISABLED, self.gargoyle['test'].status)

    def test_update_switch_status_to_default(self):
        Switch.objects.create(key='test', status=DISABLED)
        self.assertEqual(DISABLED, self.gargoyle['test'].status)

        call_command('add_switch', 'test')

        self.assertEqual(GLOBAL, self.gargoyle['test'].status)


class CommandRemoveSwitchTestCase(TestCase):

    def setUp(self):
        self.gargoyle = SwitchManager(Switch, key='key', value='value', instances=True, auto_create=True)

    def test_requires_single_arg(self):
        too_few_too_many = [
            [],
            ['one', 'two'],
        ]
        for args in too_few_too_many:
            command = RemoveSwitchCmd()

            self.assertRaises(CommandError, command.handle, *args)

    def test_removes_switch(self):
        Switch.objects.create(key='test')
        self.assertTrue('test' in self.gargoyle)

        call_command('remove_switch', 'test')

        self.assertFalse('test' in self.gargoyle)

    def test_remove_non_switch_doesnt_error(self):
        self.assertFalse('idontexist' in self.gargoyle)

        call_command('remove_switch', 'idontexist')

        self.assertFalse('idontexist' in self.gargoyle)


class HelpersTestCase(TestCase):

    def setUp(self):
        self.old_gargoyle_helpers = sys.modules.pop('gargoyle.helpers')
        del gargoyle.helpers

        self.old_json = sys.modules.pop('json')
        sys.modules['json'] = None

    def tearDown(self):
        if self.old_json is not None:
            sys.modules['json'] = self.old_json
        else:
            del sys.modules['json']
        sys.modules['gargoyle.helpers'] = self.old_gargoyle_helpers
        gargoyle.helpers = self.old_gargoyle_helpers

    def test_json_compat(self):
        # fake json module so test works even in python 2.5
        mock_json = object()
        sys.modules['json'] = mock_json

        # reload helpers module to get json reimported
        from gargoyle.helpers import json
        self.assertEqual(json, mock_json)

    def test_simplejson_compat(self):
        # reload helpers module to get json reimported
        from gargoyle.helpers import json
        self.assertEqual(json, simplejson)
