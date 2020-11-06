
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


from ansible.errors import AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.config_normalizing.base import ConfigNormalizerBaseMerger, NormalizerBase, NormalizerNamed, DefaultSetterConstant, DefaultSetterOtherKey
from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import setdefault_none, SUBDICT_METAKEY_ANY

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert


class ConfigRootNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
           'random_pwlen', DefaultSetterConstant(80)
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ServersNormalizer(pluginref),
        ]

        super(ConfigRootNormalizer, self).__init__(pluginref, *args, **kwargs)


class ServersNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ServerInstanceNormalizer(pluginref),
        ]

        super(ServersNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['servers']


class ServerInstancesNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SrvInstNormalizer(pluginref),
        ]

        super(ServerInstancesNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['instances']


class SrvInstNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ServerUsersNormalizer(pluginref),
        ]

        super(SrvInstNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return [SUBDICT_METAKEY_ANY]


class ServerUsersNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ServerBotsNormalizer(pluginref),
          ServerHumansNormalizer(pluginref),
        ]

        super(ServerUsersNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['users']


class ServerUsrBaseNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SrvUsrNormalizer(pluginref),
        ]

        super(ServerUsrBaseNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )


class ServerBotsNormalizer(ServerUsrBaseNormalizer):

    def __init__(self, pluginref, *args, **kwargs):
        super(ServerBotsNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['bots']


class ServerHumansNormalizer(ServerUsrBaseNormalizer):

    def __init__(self, pluginref, *args, **kwargs):
        super(ServerHumansNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['humans']


class SrvUsrNormalizer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
           'pw_access', DefaultSetterConstant(True)
        )

        super(SrvUsrNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

        self.default_setters['name'] = DefaultSetterOtherKey('username')

    @property
    def config_path(self):
        return [SUBDICT_METAKEY_ANY]

    @property
    def name_key(self):
        return 'username'


class ActionModule(ConfigNormalizerBaseMerger):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(ConfigRootNormalizer(self), 
            *args, default_merge_vars=['gitlab_cfg_defaults'], 
            extra_merge_vars_ans=['extra_gitlab_config_maps'], 
            **kwargs
        )

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def my_ansvar(self):
        return 'normalize_gitlab_cfg'

