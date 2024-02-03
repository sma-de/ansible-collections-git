
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


class GitServerBaseNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])

        ##
        ## note: prepend this as it must come before
        ##   any downstream set normers
        ##
        subnorms.insert(0, ConnectionNormalizer(pluginref))

        super(GitServerBaseNormer, self).__init__(pluginref, *args, **kwargs)


class ConnectionNormalizer(NormalizerBase):

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

