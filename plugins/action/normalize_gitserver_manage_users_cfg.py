
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

from ansible_collections.smabot.git.plugins.module_utils.plugins.config_normalizing.gitserver_norm_base import \
  GitServerBaseNormer,\
  SafeDict

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


class ConfigRootNormalizer(GitServerBaseNormer):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          UserInstNormer(pluginref),
        ]

        super(ConfigRootNormalizer, self).__init__(pluginref, *args, **kwargs)



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
          UserPATokensAll_Normer(pluginref),
          UserMembershipsNormer(pluginref),
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
        scfg_um = my_subcfg['_export_configs']['user_manage']

        if my_subcfg['user_type'] == 'service_account':
            ##
            ## note: service-accounts are passwordless as you
            ##   cannot login with them anyway (only use api
            ##   token and ssh keys)
            ##
            setdefault_none(my_subcfg, 'credentials', {}).update(
               password={'disabled': True}
            )

            ##
            ## on default make bot users maximal restricted
            ## by also applying the external flag
            ##
            setdefault_none(scfg_um, 'external', True)

        em = my_subcfg.get('email', None)
        fmt_mail = None

        if em:
            setdefault_none(em, 'user', un)
            setdefault_none(em, 'format', '{user}@{domain}')

            ##
            ## optionally support normalisation of email parts
            ## by a simple pattern-replacement mapping
            ##
            n = setdefault_none(em, 'norming', {})
            for k, v in n.items():
                nv = em[k]

                for pat, repl in v.items():
                    nv = nv.replace(pat, repl)

                em[k] = nv

            fmt_mail = em['format'].format(**em)

        else:

            ansible_assert(not mail_needed,
                "providing a mail address for user '{}' is mandatory,"\
                " so you must at least specify a domain".format(un)
            )

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



class UserMembershipsNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs,
          'exclusive', DefaultSetterConstant(False)
        )

        self._add_defaultsetter(kwargs,
          'enable', DefaultSetterConstant(None)
        )

        self._add_defaultsetter(kwargs,
          'default_role', DefaultSetterConstant(None)
        )

        self._add_defaultsetter(kwargs,
          'forced_role', DefaultSetterConstant(None)
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          (IdentityGroupNormer, True),
          UserMembershipsTargetInstNormer(pluginref),
          UserMembershipsExclusiveNormer(pluginref),
        ]

        super(UserMembershipsNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['memberships']


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        ena = my_subcfg['enable']

        if ena is None:
            ## default enable depending on other settings
            ena = my_subcfg['exclusive']\
                or bool(my_subcfg.get('identity_group', None))\
                or bool(my_subcfg.get('targets', None))

            my_subcfg['enable'] = ena

        if not ena:
            return my_subcfg

        return my_subcfg


    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        ##
        ## combine all target config parts to single common config
        ##

        upcfg = {
          'groups': {'groups': {}},
          'projects': {'projects': {}},
        }

        for k in list(my_subcfg['targets'].keys()):
            v = my_subcfg['targets'][k]

            if not v:
                ##
                ## ignore and remove disabled targets
                ##
                my_subcfg['targets'].pop(k)
                continue

            uptype = next(iter(v['_upstream_cfg']))
            upcfg[uptype][uptype].update(v['_upstream_cfg'][uptype])

        if not upcfg['groups']['groups']:
            upcfg.pop('groups')

        if not upcfg['projects']['projects']:
            upcfg.pop('projects')

        if upcfg:
            my_subcfg['_upstream_cfg_create_memberships'] = upcfg

        return my_subcfg



class UserMembershipsExclusiveNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'enable', DefaultSetterConstant(True)
        )

        super(UserMembershipsExclusiveNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['exclusive']

    @property
    def simpleform_key(self):
        return 'enable'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        if not my_subcfg['enable']:
            return my_subcfg

        cfgs = setdefault_none(my_subcfg, 'configs', {})
        ucfg = setdefault_none(cfgs, 'user', {})

        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)

        ucfg['username'] = pcfg['username']

        pcfg = self.get_parentcfg(cfg, cfgpath_abs)

        idgroup = pcfg.get('identity_group', None)

        if idgroup:
            ##
            ## if we have an identity group defined and
            ## have exclusive mode set, means that the user
            ## itself is only allowed to be a direct member
            ## in id-group, every other membership should
            ## be handled by id-group
            ##
            ucfg['ignores'] = [idgroup['full_path']]
        else:
            ##
            ## in setups without id-groups user membership is
            ## obviously okay for every explicitly defined target
            ##
            ucfg['ignores'] = []

            for k, v in pcfg['targets'].items():
                ucfg['ignores'].append(v['full_path'])

        gcfg = setdefault_none(cfgs, 'groups', {})

        if idgroup:
            gcfg['group_id'] = idgroup['full_path']
            gcfg['inverted'] = True
            gcfg['ignores'] = []

            ##
            ## on default dont fail on searching group
            ## mappings because id-group does not exist yet
            ##
            setdefault_none(gcfg, 'optional', True)

            for k, v in pcfg['targets'].items():
                if not v:
                    ## ignore disabled targets
                    continue

                if v['full_path'] == idgroup['full_path']:
                    continue

                gcfg['ignores'].append(v['full_path'])

        return my_subcfg



class IdentityGroupNormer(NormalizerBase):

    NORMER_CONFIG_PATH = ['identity_group']

    @property
    def config_path(self):
        return self.NORMER_CONFIG_PATH

    @property
    def simpleform_key(self):
        return 'full_path'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        fp = my_subcfg['full_path']

        ## template variables inside fullpath

        ## collect variables
        templ_vars = {}

        ## add templatable user attributes
        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)

        for x in ['username']:
            templ_vars[x] = pcfg[x]

        fp = fp.format(**templ_vars)

        my_subcfg['full_path'] = fp
        my_subcfg['type'] = 'group'
        my_subcfg['identity_group'] = True

        pcfg = self.get_parentcfg(cfg, cfgpath_abs)
        targets = setdefault_none(pcfg, 'targets', {})

        targets[fp] = my_subcfg
        return my_subcfg



class UserMembershipsTargetInstNormer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'identity_group', DefaultSetterConstant(False)
        )

        super(UserMembershipsTargetInstNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['targets', SUBDICT_METAKEY_ANY]

    @property
    def name_key(self):
        return 'full_path'

    @property
    def simpleform_key(self):
        return 'role'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        fp = my_subcfg['full_path']
        deftype = 'project'

        if fp[-1] == '/':
            fp = fp[:-1]
            my_subcfg['full_path'] = fp
            deftype = 'group'

        setdefault_none(my_subcfg, 'type', deftype)

        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)

        identgrp = pcfg.get('identity_group', None)
        is_identgrp = my_subcfg['identity_group']

        ## build upstream config for upstream module
        ## handling membership settings
        c = setdefault_none(my_subcfg, 'config', {})

        ##
        ## on default dont manage stuff like creating
        ## or modifying group/project itself, only its members
        ##
        tmp = setdefault_none(c, 'basic_management', {})
        setdefault_none(tmp, 'enable', is_identgrp)

        ##
        ## on default disable basic management for all implicit base groups
        ##
        grpgen = setdefault_none(c, 'grpgen', {})
        setdefault_none(setdefault_none(grpgen, 'overwrites_all', {}),
           'basic_management', False
        )

        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)

        tmp = {
          'default_role': pcfg['default_role'],
          'forced_role': pcfg['forced_role'],
        }

        if not identgrp or is_identgrp:

            pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=3)
            tmp['users'] = {
              'members': {pcfg['username']: my_subcfg.get('role', None)}
            }

        else:

            tmp['groups'] = {
              'members': {identgrp['full_path']: my_subcfg.get('role', None)}
            }

        c['members'] = tmp

        my_subcfg['_upstream_cfg'] = {
           (my_subcfg['type'] + 's'): {
              fp: c,
           },
        }

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

    @property
    def storeable(self):
        return True

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        if self.default_settings_distance:
            pcfg = self.get_parentcfg(cfg, cfgpath_abs,
              level=self.default_settings_distance
            )

            my_subcfg = merge_dicts(copy.deepcopy(
               pcfg['default_settings']), my_subcfg
            )

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

        setdefault_none(ac, 'enabled', True)

        ansible_assert(value or ac['enabled'],
           "bad user definition: credential must either have"\
           " an explicit value set or 'auto_create' must be active"
        )

        ansible_assert(not (value and ac['enabled']),
           "bad user definition: either give a credential value"\
           " explicitly or activate 'auto_create', but never do"\
           " both at the same time"
        )

        setdefault_none(ac, 'cycle', True)
        my_subcfg['auto_create'] = ac

        if self.storeable:
            stores = my_subcfg.get('stores', None)

            if my_subcfg['auto_create']['enabled'] and not stores:
                # create ansible variable default store
                stores = {'ansible_variables': None}
                my_subcfg['stores'] = stores

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

        if self.has_value:
            ##
            ## optionally if credential has optional extra_values
            ## default there credstore keyname by their cfg keynames
            ##
            for k in my_subcfg.get('extra_values', {}):
                knames[k] = k

        for k in knames:
            repl = SafeDict()
            repl.update(**self._get_store_keynames_replacements(
              cfg, my_subcfg, cfgpath_abs, store_id, store_map
            ))
            
            v = knames[k].format_map(repl)

            knames[k] = v

        if self.has_value:
            credstore_extra_vals = {}

            for k, v in my_subcfg.get('extra_values', {}).items():
                credstore_extra_vals[knames[k]] = v

            store_map['parameters']['extra_values'] = credstore_extra_vals


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

        if self.storeable:
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
                   " one of them explicitly, found '{}' stores"\
                   " defined:\n{}".format(len(my_subcfg['stores']),
                      my_subcfg['stores']
                   )
                )

                ansible_assert(len(default_stores) == 1,
                   "only one secret store can be default, but we"\
                   " found {}:\n{}".format(len(default_stores), default_stores)
                )

            my_subcfg['_default_store'] = default_stores[0]

        return my_subcfg


class UserCredsDefaults_Normer(CredentialSettingsNormerBase):

    @property
    def config_path(self):
        return ['credentials', 'default_settings']

    @property
    def has_value(self):
        return False

    @property
    def storeable(self):
        return False

##    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
##        my_subcfg = super()._handle_specifics_presub(cfg, my_subcfg, cfgpath_abs)
##
##        ##
##        ## update: bad idea of doing that here, what if a config only has
##        ##   store explicitly defined for specific credentials
##        ##
##        ##stores = my_subcfg['stores']
##
##        ##if my_subcfg['auto_create']['enabled'] and not stores:
##        ##    # create ansible variable default store
##        ##    stores = {'ansible_variables': None}
##        ##    my_subcfg['stores'] = stores
##
##        return my_subcfg


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
        setdefault_none(vnames, 'token', 'token_{cred_id}')

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



class UserPATokensAll_Normer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs,
          'config', DefaultSetterConstant({})
        )

        self._add_defaultsetter(kwargs,
          'exclusive', DefaultSetterConstant(False)
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          UserPATokenInst_Normer(pluginref),
        ]

        super(UserPATokensAll_Normer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['credentials', 'tokens', 'personal_access']

    def _handle_specifics_postsub_gitlab(self, cfg, my_subcfg, cfgpath_abs):
        c = my_subcfg['config']
        c['exclusive'] = my_subcfg['exclusive']

        cfgmap = {}

        for k,v in my_subcfg['tokens'].items():
            cfgmap[v['name']] = v['config']

        c['user_tokens'] = cfgmap

        pcfg_usr = self.get_parentcfg(cfg, cfgpath_abs, level=3)
        c['username'] = pcfg_usr['username']


    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        if not my_subcfg['tokens'] and not my_subcfg['exclusive']:
            return my_subcfg

        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=5)
        srvtype = pcfg['server_type']

        tmp = getattr(self, '_handle_specifics_postsub_' + srvtype, None)

        ansible_assert(tmp,
           "unsupported git server type '{}'".format(srvtype)
        )

        tmp(cfg, my_subcfg, cfgpath_abs)
        return my_subcfg



class UserPATokenInst_Normer(NormalizerNamed, CredentialSettingsNormerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'config', DefaultSetterConstant({})
        )

        super(UserPATokenInst_Normer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def default_settings_distance(self):
        return 4

    @property
    def has_value(self):
        return False

    @property
    def config_path(self):
        return ['tokens', SUBDICT_METAKEY_ANY]

    @property
    def simpleform_key(self):
        return 'scopes'

    def _get_store_keynames_replacements(self, cfg, my_subcfg, cfgpath_abs,
        store_id, store_map
    ):
        ## optionally overwriteable by subclasses
        return {'cred_id': cfgpath_abs[-1]}


    def _handle_specifics_presub_gitlab(self, cfg, my_subcfg, cfgpath_abs):
        c = my_subcfg['config']
        defstate = 'present'

        if my_subcfg['auto_create']['cycle']:
            defstate = 'update'

        c['state'] = defstate

        tmp = my_subcfg.get('type', None)
        if tmp:
            c['type'] = tmp


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        my_subcfg = super()._handle_specifics_presub(cfg, my_subcfg, cfgpath_abs)

        scp = my_subcfg['scopes']

        if isinstance(scp, string_types):
            scp = {scp: None}

        tmp = []

        ## normalize scopes to list
        for k, v in scp.items():
            if v is None:
                v = True

            if not v:
                continue

            tmp.append(k)

        scp = tmp

        my_subcfg['scopes'] = scp

        c = my_subcfg['config']
        c['scopes'] = scp

        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=7)
        srvtype = pcfg['server_type']

        tmp = getattr(self, '_handle_specifics_presub_' + srvtype, None)

        ansible_assert(tmp,
           "unsupported git server type '{}'".format(srvtype)
        )

        tmp(cfg, my_subcfg, cfgpath_abs)
        return my_subcfg



class UserSshKey_Normer(CredentialSettingsNormerBase):

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



class ActionModule(ConfigNormalizerBaseMerger):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(ConfigRootNormalizer(self), *args,
            default_merge_vars=[
               'smabot_git_gitserver_manage_users_args_defaults'
            ],
            extra_merge_vars_ans=[
               'extra_smabot_git_gitserver_manage_users_args_config_maps'
            ],
            **kwargs
        )

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def my_ansvar(self):
        return 'smabot_git_gitserver_manage_users_args'

