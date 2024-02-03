
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import collections
import copy


from ansible.errors import AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.config_normalizing.base import ConfigNormalizerBaseMerger, NormalizerBase, NormalizerNamed, DefaultSetterConstant, DefaultSetterOtherKey
from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  get_subdict, \
  merge_dicts, \
  setdefault_none, \
  SUBDICT_METAKEY_ANY

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


class SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


class ConfigRootNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ConnectionNormalizer(pluginref),
          UserInstNormer(pluginref),
        ]

        super(ConfigRootNormalizer, self).__init__(pluginref, *args, **kwargs)


class ConnectionNormalizer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        ##subnorms = kwargs.setdefault('sub_normalizers', [])
        ##subnorms += [
        ##  ServerInstancesNormalizer(pluginref),
        ##]

        super(ConnectionNormalizer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['connection']


    ## TODO: support env vars???
    def _handle_server_var(self, var, srvtype, basemap,
        mapkey, publish_ansvars, optional=False,
    ):
        ## check if cfgmap has an explicit value set, if so prefer that
        val = basemap.get(mapkey, None)

        ## as we got the value from cfgmap we must create corresponding ansvars
        setvars = True

        test_vars = [
          var + '_' + srvtype,
        ]

        if not val:
            ## 2nd source: server specific var
            val = self.pluginref.get_ansible_var(test_vars[-1], None)

            ## connection credentials are already avaible as most specific
            ## variables, dont recreate them, it would not really hurt,
            ## but security wise it gives a little more theoretically
            ## exposure to confidential data than necessary, so dont do it
            setvars = False

            if not val:
                ## final fallback source: server agnostic general var
                test_vars.append(var)
                val = self.pluginref.get_ansible_var(test_vars[-1], None)

                ansible_assert(val or optional,\
                   "mandatory connection attribute '{}' not found, set"\
                   " it either directly in cfgmap or by using one of"\
                   " these ansible variables: {}".format(mapkey, test_vars)
                )

        if val and setvars:
            for x in test_vars:
                publish_ansvars[x] = val


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        pcfg = self.get_parentcfg(cfg, cfgpath_abs)
        srvtype = my_subcfg['type']

        pcfg['server_type'] = srvtype

        ##
        ## note: internally we handle connection credentials by ansible
        ##   vars which might or might not yet be set, normalize this here
        ##
        publish_ansvars = {}

        tmp = [
          ('url', 'auth_gitserver_url', False),
          ('validate_certs', 'auth_gitserver_valcerts', True),
        ]

        for mk, avar, opt in tmp:
            self._handle_server_var(avar, srvtype,
              my_subcfg, mk, publish_ansvars, opt
            )

        auth = setdefault_none(my_subcfg, 'auth', {})

        tmp = [
          ('token', 'auth_gitserver_token', True),
          ('username', 'auth_gitserver_user', True),
          ('password', 'auth_gitserver_pw', True),
        ]

        for mk, avar, opt in tmp:
            self._handle_server_var(avar, srvtype,
              auth, mk, publish_ansvars, opt
            )

        my_subcfg['_export_vars'] = {
          'ansible': publish_ansvars,
        }

        return my_subcfg


class UserInstNormer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'user_type', DefaultSetterConstant('standard')
        )

        self._add_defaultsetter(kwargs, 
          'config', DefaultSetterConstant({})
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          UserCredsDefaults_Normer(pluginref),
          UserPW_Normer(pluginref),
          UserSshKey_Normer(pluginref),
        ]

        super(UserInstNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def name_key(self):
        return 'username'


    @property
    def config_path(self):
        return ['users', SUBDICT_METAKEY_ANY]


    def _handle_specifics_presub_gitlab(self, cfg, my_subcfg, cfgpath_abs):
        mail_needed = True
        un = my_subcfg['username']

        if my_subcfg['user_type'] == 'service_account':
            ##
            ## note: service-accounts are passwordless as you
            ##   cannot login with them anyway (only use api
            ##   token and ssh keys)
            ##
            setdefault_none(my_subcfg, 'credentials', {}).update(
               password={'disabled': True}
            )

        em = my_subcfg.get('email', None)
        fmt_mail = None

        if em:
            setdefault_none(em, 'user', un)
            setdefault_none(em, 'format', '{user}@{domain}')

            fmt_mail = em['format'].format(**em)

        else:

            ansible_assert(not mail_needed,
                "providing a mail address for user '{}' is mandatory,"\
                " so you must at least specify a domain".format(un)
            )

        scfg_um = my_subcfg['_export_configs']['user_manage']

        ##
        ## note: in gitlab api the attribute "name" is used for the
        ##   real person name (e.g. "Jane Doe") while the attribute
        ##   "username" describes the internally used unique user
        ##   id string, so mandatory important in theory seems to
        ##   be only "username", but it seems api actually always
        ##   need both set, so we default name to username
        ##
        setdefault_none(scfg_um, 'name', un)

        if fmt_mail:
            scfg_um['email'] = fmt_mail

        scfg_um['user_type'] = my_subcfg['user_type']
        return my_subcfg


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)

        srvtype = pcfg['server_type']

        tmp = getattr(self, '_handle_specifics_presub_' + srvtype, None)

        ansible_assert(tmp,
           "unsupported git server type '{}'".format(srvtype)
        )

        scfg_um = setdefault_none(my_subcfg['config'], 'user_manage', {})
        scfg_um['username'] = my_subcfg['username']

        my_subcfg['_export_configs'] = {
          'user_manage': scfg_um,
        }

        my_subcfg = tmp(cfg, my_subcfg, cfgpath_abs)
        return my_subcfg


class CredentialSettingsNormerBase(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'stores', DefaultSetterConstant({})
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          CredentialStoreInstNormer(pluginref),
        ]

        super(CredentialSettingsNormerBase, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def default_settings_distance(self):
        return 0

    @property
    def has_value(self):
        return True

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        ac = my_subcfg.get('auto_create', None)
        value = None

        if self.has_value:
            value = my_subcfg.get('value', None)

        if ac is None:
            ## default auto-create to false if an explicit
            ## value is provided and true otherwise
            ac = value is None

        if not isinstance(ac, collections.abc.Mapping):
            ## assume simple bool
            ac = {'enabled': ac}

        ansible_assert(value or ac['enabled'],
           "bad user definition: credential must either have"\
           " an explicit value set or 'auto_create' must be active"
        )

        ansible_assert(not (value and ac['enabled']),
           "bad user definition: either give a credential value"\
           " explicitly or activate 'auto_create', but never do"\
           " both at the same time"
        )

        ## TODO: make this true ultimatively
        setdefault_none(ac, 'cycle', False)

        my_subcfg['auto_create'] = ac

        if self.default_settings_distance:
            pcfg = self.get_parentcfg(cfg, cfgpath_abs,
              level=self.default_settings_distance
            )

            my_subcfg = merge_dicts(copy.deepcopy(
               pcfg['default_settings']), my_subcfg
            )

        return my_subcfg


    def _get_store_keynames_replacements(self, cfg, my_subcfg, cfgpath_abs,
        store_id, store_map
    ):
        ## optionally overwriteable by subclasses
        return {}


    def _postsub_mod_credstore_ansible_variables(self, cfg, my_subcfg, cfgpath_abs,
        store_id, store_map
    ):
        ## optionally change store keynames based on credential
        ## specific meta information
        knames = store_map['parameters']['key_names']
        for k in knames:
            repl = SafeDict()
            repl.update(**self._get_store_keynames_replacements(
              cfg, my_subcfg, cfgpath_abs, store_id, store_map
            ))
            
            v = knames[k].format_map(repl)

            knames[k] = v


    def _postsub_mod_credstore_hashivault(self, cfg, my_subcfg, cfgpath_abs,
        store_id, store_map
    ):
        self._postsub_mod_credstore_ansible_variables(
           cfg, my_subcfg, cfgpath_abs, store_id, store_map
        )


    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        ## optionally normalize adapt generic cred stores settings
        ## to specific credential
        for k,v in my_subcfg.get('stores', {}).items():
            tmp = getattr(self, '_postsub_mod_credstore_' + v['type'], None)

            if tmp:
                tmp(cfg, my_subcfg, cfgpath_abs, k, v)

        if self.has_value:
            store_cnt = len(my_subcfg['stores'])

            if my_subcfg['auto_create']['enabled']:
                ansible_assert(store_cnt > 0,
                   "when auto generating secrets we need at least"\
                   " one store defined to export secrets to"
                )

            if store_cnt > 0:
                ## check there is at exactly 1 default store,
                ##   if only one is defined, make it auto default
                default_stores = []

                for k, v in my_subcfg['stores'].items():
                    if store_cnt == 1:
                        v['default'] = True

                    if v['default']:
                        default_stores.append(v)

                ansible_assert(default_stores,
                   "one secret store must be marked as default, if there is"\
                   " only one it should be made default automatically, if you"\
                   " defined more than one, you must set default to true for"\
                   " one of them explicitly"
                )

                ansible_assert(len(default_stores) == 1,
                   "only one secret store can be default, but we"\
                   " found {}:\n{}".format(len(default_stores), default_stores)
                )

            my_subcfg['_default_store'] = default_stores[0]

        return my_subcfg


class UserCredsDefaults_Normer(CredentialSettingsNormerBase):

    def __init__(self, pluginref, *args, **kwargs):
        ##subnorms = kwargs.setdefault('sub_normalizers', [])
        ##subnorms += [
        ##  SrvInstNormalizer(pluginref),
        ##]

        super(UserCredsDefaults_Normer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['credentials', 'default_settings']

    @property
    def has_value(self):
        return False

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        my_subcfg = super()._handle_specifics_presub(cfg, my_subcfg, cfgpath_abs)

        stores = my_subcfg['stores']

        if my_subcfg['auto_create']['enabled'] and not stores:
            # create ansible variable default store
            stores = {'ansible_variables': None}
            my_subcfg['stores'] = stores

        return my_subcfg


class CredentialStoreInstNormer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'default', DefaultSetterConstant(False)
        )

        self._add_defaultsetter(kwargs, 
          'parameters', DefaultSetterConstant({})
        )

        super(CredentialStoreInstNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['stores', SUBDICT_METAKEY_ANY]


    def _handle_specifics_presub_ansible_variables(self, cfg, my_subcfg, cfgpath_abs):
        vnames = setdefault_none(my_subcfg['parameters'], 'key_names', {})

        setdefault_none(vnames, 'basevar',
          'smabot_git_gitserver_manage_users_credentials'
        )

        setdefault_none(vnames, 'password', 'password')
        setdefault_none(vnames, 'sshkey_public', 'sshkey_public_{cred_id}')
        setdefault_none(vnames, 'sshkey_private', 'sshkey_private_{cred_id}')

        return my_subcfg


    def _handle_specifics_presub_hashivault(self, cfg, my_subcfg, cfgpath_abs):
        my_subcfg = self._handle_specifics_presub_ansible_variables(
          cfg, my_subcfg, cfgpath_abs
        )

        setdefault_none(my_subcfg, 'config', {})

        params = my_subcfg['parameters']

        params['key_names'].pop('basevar')

        psets = setdefault_none(params, 'settings', {})
        psets_defs = setdefault_none(psets, 'defaults', {})
        psets_defs_read = setdefault_none(psets_defs, 'read', {})

        setdefault_none(psets_defs_read, 'optional', True)

        return my_subcfg


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        stype = setdefault_none(my_subcfg, 'type', my_subcfg['name'])

        tmp = getattr(self, '_handle_specifics_presub_' + stype, None)

        if tmp:
            my_subcfg = tmp(cfg, my_subcfg, cfgpath_abs)

        return my_subcfg


class UserPW_Normer(CredentialSettingsNormerBase):

    def __init__(self, pluginref, *args, **kwargs):
        ## self._add_defaultsetter(kwargs, 
        ##   'type', DefaultSetterConstant('standard')
        ## )

        ## ##subnorms = kwargs.setdefault('sub_normalizers', [])
        ## ##subnorms += [
        ## ##  SrvInstNormalizer(pluginref),
        ## ##]

        super(UserPW_Normer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def default_settings_distance(self):
        return 1

    @property
    def config_path(self):
        return ['credentials', 'password']

    @property
    def simpleform_key(self):
        return 'value'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        my_subcfg = super()._handle_specifics_presub(cfg, my_subcfg, cfgpath_abs)

        ac_cfg = setdefault_none(my_subcfg['auto_create'], 'config', {})

        setdefault_none(ac_cfg, 'length', 80)

        return my_subcfg


class UserSshKey_Normer(CredentialSettingsNormerBase):

    def __init__(self, pluginref, *args, **kwargs):
        ## self._add_defaultsetter(kwargs, 
        ##   'type', DefaultSetterConstant('standard')
        ## )

        ## ##subnorms = kwargs.setdefault('sub_normalizers', [])
        ## ##subnorms += [
        ## ##  SrvInstNormalizer(pluginref),
        ## ##]

        super(UserSshKey_Normer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def default_settings_distance(self):
        return 2

    @property
    def config_path(self):
        return ['credentials', 'ssh_keys', SUBDICT_METAKEY_ANY]

    @property
    def simpleform_key(self):
        return 'value'


    def _get_store_keynames_replacements(self, cfg, my_subcfg, cfgpath_abs,
        store_id, store_map
    ):
        ## optionally overwriteable by subclasses
        return {'cred_id': cfgpath_abs[-1]}

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        my_subcfg = super()._handle_specifics_presub(cfg, my_subcfg, cfgpath_abs)

        val = my_subcfg.get('value', None)

        if val is not None:
            if not isinstance(val, collections.abc.Mapping):
                ## assume simple string containing public ssh key
                val = {'public': val}
                my_subcfg['value'] = val

        ac_cfg = setdefault_none(my_subcfg['auto_create'], 'config', {})
        ##setdefault_none(ac_cfg, 'length', 80)

        return my_subcfg


## class SrvInstNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           ServerUsersNormalizer(pluginref),
##           SrvRolesNormalizer(pluginref),
##         ]
## 
##         super(SrvInstNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return [SUBDICT_METAKEY_ANY]
## 
## 
## class SrvRolesBaseNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           SrvRolesMembersNormalizer(pluginref),
## 
##           ## note: for recursive structures, the sub normalizers can only 
##           ##   be instantiated if the corresponding key actually exists 
##           ##   to avoid indefinite recursions of death
##           (SrvSubRolesNormalizer, True),
##         ]
## 
##         super(SrvRolesBaseNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
##         # do config subkey
##         c = setdefault_none(my_subcfg, 'config', defval={})
##         setdefault_none(c, 'name', defval=cfgpath_abs[-1])
## 
##         # build role hierarchy path and parent
##         if cfgpath_abs[-1] == 'roles':
##             ## top level
##             parent = []
##         else:
##             ## subrole
##             parent = get_subdict(cfg, cfgpath_abs[:-2])
##             parent = parent['role_abspath']
## 
##         my_subcfg['role_abspath'] = parent + [c['name']]
##         c['parent'] = '/'.join(parent)
## 
##         return my_subcfg
## 
## 
## class SrvRolesNormalizer(SrvRolesBaseNormalizer):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(SrvRolesNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return ['roles']
## 
## 
## class SrvSubRolesNormalizer(NormalizerBase):
## 
##     NORMER_CONFIG_PATH = ['subroles']
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           SrvRoleInstNormalizer(pluginref),
##         ]
## 
##         super(SrvSubRolesNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return type(self).NORMER_CONFIG_PATH
## 
## 
## class SrvRoleInstNormalizer(SrvRolesBaseNormalizer):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(SrvRoleInstNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return [SUBDICT_METAKEY_ANY]
## 
## 
## class SrvRolesMembersNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(SrvRolesMembersNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return ['members']
## 
##     def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
##         if not my_subcfg:
##             return my_subcfg
## 
##         ## if it exists, members should be a dict where the keys are 
##         ## valid gitlab access levels (like guest or developer) and 
##         ## the values should be a list of users
##         exportcfg = []
##         my_group = self.get_parentcfg(cfg, cfgpath_abs)
##         my_group = '/'.join(my_group['role_abspath'])
## 
##         for (k,ul) in iteritems(my_subcfg):
##             for u in ul:
##                 exportcfg.append({
##                   'gitlab_group': my_group, 'gitlab_user': u, 'access_level': k
##                 })
## 
##         my_subcfg['_exportcfg'] = exportcfg
## 
##         return my_subcfg
## 
## 
## class ServerUsersNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           ServerBotsNormalizer(pluginref),
##           ServerHumansNormalizer(pluginref),
##         ]
## 
##         super(ServerUsersNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return ['users']
## 
## 
## class ServerUsrBaseNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           SrvUsrNormalizer(pluginref),
##         ]
## 
##         super(ServerUsrBaseNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
## 
## class ServerBotsNormalizer(ServerUsrBaseNormalizer):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(ServerBotsNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return ['bots']
## 
## 
## class ServerHumansNormalizer(ServerUsrBaseNormalizer):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(ServerHumansNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return ['humans']
## 
## 
## class SrvUsrNormalizer(NormalizerBase):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         subnorms = kwargs.setdefault('sub_normalizers', [])
##         subnorms += [
##           SrvUsrCfgNormalizer(pluginref),
##         ]
## 
##         self._add_defaultsetter(kwargs, 
##            'pw_access', DefaultSetterConstant(True)
##         )
## 
##         super(SrvUsrNormalizer, self).__init__(
##            pluginref, *args, **kwargs
##         )
## 
##     @property
##     def config_path(self):
##         return [SUBDICT_METAKEY_ANY]
## 
##     def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
##         usr_roles = my_subcfg.get('roles', None)
## 
##         if usr_roles:
##             for ur in usr_roles:
##                 user_role_to_cfg(my_subcfg['config']['username'], ur, 
##                   self.get_parentcfg(cfg, cfgpath_abs, level=3)
##                 )
## 
##         return my_subcfg
## 
## 
## class SrvUsrCfgNormalizer(NormalizerNamed):
## 
##     def __init__(self, pluginref, *args, **kwargs):
##         super(SrvUsrCfgNormalizer, self).__init__(
##            pluginref, *args, mapkey_lvl=-2, **kwargs
##         )
## 
##         self.default_setters['name'] = DefaultSetterOtherKey('username')
## 
##     @property
##     def config_path(self):
##         return ['config']
## 
##     @property
##     def name_key(self):
##         return 'username'
## 
##     def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
##         mail = my_subcfg.get('email', None)
## 
##         if not mail:
##             # if not mail address is explicitly given, check if mail 
##             # template is specified for server, if so use this to 
##             # create address with username as param
##             tmp = self.get_parentcfg(
##                 cfg, cfgpath_abs, level=3
##             ).get('mail_template', None)
## 
##             if tmp:
##                 my_subcfg['email'] = tmp.format(
##                    my_subcfg['username'].replace('_', '-')
##                 )
## 
##         return my_subcfg



class ActionModule(ConfigNormalizerBaseMerger):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(ConfigRootNormalizer(self), 
            *args, ##default_merge_vars=['gitlab_cfg_defaults'], 
            ##extra_merge_vars_ans=['extra_gitlab_config_maps'], 
            **kwargs
        )

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def my_ansvar(self):
        return 'smabot_git_gitserver_manage_users_args'

