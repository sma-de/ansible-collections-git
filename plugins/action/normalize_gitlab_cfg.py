
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


from ansible.errors import AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.config_normalizing.base import ConfigNormalizerBaseMerger, NormalizerBase, NormalizerNamed, DefaultSetterConstant, DefaultSetterOtherKey
from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import setdefault_none, SUBDICT_METAKEY_ANY, get_subdict

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert



def user_role_to_cfg(username, urole, cfg):
    tmp = urole['path'].replace('/', '/subroles/').split('/')

    tmp = get_subdict(cfg, tmp, default_empty=True)
    setdefault_none(setdefault_none(tmp, 'members', {}), 
       urole['level'], []
    ).append(username)


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
          ServerInstancesNormalizer(pluginref),
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
          SrvRolesNormalizer(pluginref),
        ]

        super(SrvInstNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return [SUBDICT_METAKEY_ANY]


class SrvRolesBaseNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SrvRolesMembersNormalizer(pluginref),
        ]

        ## note: for recursive structures, the sub normalizers can only 
        ##   be instantiated if the corresponding key actually exists 
        ##   to avoid indefinite recursions of death
        lazy_subnorms = kwargs.setdefault('sub_normalizers_lazy', [])
        lazy_subnorms += [
          SrvSubRolesNormalizer,
        ]

        super(SrvRolesBaseNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        # do config subkey
        c = setdefault_none(my_subcfg, 'config', defval={})
        setdefault_none(c, 'name', defval=cfgpath_abs[-1])

        # build role hierarchy path and parent
        if cfgpath_abs[-1] == 'roles':
            ## top level
            parent = []
        else:
            ## subrole
            parent = get_subdict(cfg, cfgpath_abs[:-2])
            parent = parent['role_abspath']

        my_subcfg['role_abspath'] = parent + [c['name']]
        c['parent'] = '/'.join(parent)

        return my_subcfg


class SrvRolesNormalizer(SrvRolesBaseNormalizer):

    def __init__(self, pluginref, *args, **kwargs):
        super(SrvRolesNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['roles']


class SrvSubRolesNormalizer(NormalizerBase):

    NORMER_CONFIG_PATH = ['subroles']

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SrvRoleInstNormalizer(pluginref),
        ]

        super(SrvSubRolesNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return type(self).NORMER_CONFIG_PATH


class SrvRoleInstNormalizer(SrvRolesBaseNormalizer):

    def __init__(self, pluginref, *args, **kwargs):
        super(SrvRoleInstNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return [SUBDICT_METAKEY_ANY]


class SrvRolesMembersNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        super(SrvRolesMembersNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['members']

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        if not my_subcfg:
            return my_subcfg

        ## if it exists, members should be a dict where the keys are 
        ## valid gitlab access levels (like guest or developer) and 
        ## the values should be a list of users
        exportcfg = []
        my_group = self.get_parentcfg(cfg, cfgpath_abs)
        my_group = '/'.join(my_group['role_abspath'])

        for (k,ul) in iteritems(my_subcfg):
            for u in ul:
                exportcfg.append({
                  'gitlab_group': my_group, 'gitlab_user': u, 'access_level': k
                })

        my_subcfg['_exportcfg'] = exportcfg

        return my_subcfg


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

    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        usr_roles = my_subcfg.get('roles', None)

        if usr_roles:
            for ur in usr_roles:
                user_role_to_cfg(my_subcfg['config']['username'], ur, cfg)

        return my_subcfg


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


class SrvUsrNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SrvUsrCfgNormalizer(pluginref),
        ]

        self._add_defaultsetter(kwargs, 
           'pw_access', DefaultSetterConstant(True)
        )

        super(SrvUsrNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return [SUBDICT_METAKEY_ANY]


class SrvUsrCfgNormalizer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        super(SrvUsrCfgNormalizer, self).__init__(
           pluginref, *args, mapkey_lvl=-2, **kwargs
        )

        self.default_setters['name'] = DefaultSetterOtherKey('username')

    @property
    def config_path(self):
        return ['config']

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
        return 'gitlab_cfg'

