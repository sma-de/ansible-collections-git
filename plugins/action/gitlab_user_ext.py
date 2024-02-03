
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections
import uuid

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import GitlabUserBase
from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


##
## this class basically wraps upstream module "community.general.gitlab_user"
## and adds some extra functionality upstream module is (currently) lacking
## like handling "service_accounts"
##
class ActionModule(GitlabUserBase):

    UPSTREAM_USER_MODULE = 'community.general.gitlab_user'

    UPSTREAM_FORWARDING_PARAMS = [
      'username', 'name', 'password', 'email', 'state',
    ]


    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          ##'user_tokens': ([collections.abc.Mapping]),
          ##'strict': ([bool], False),

          'user_type': (list(string_types), 'standard', ['standard', 'service_account']),
          'extra_emails': ([collections.abc.Mapping], {}),

          ## upstream forwarding params
          'name': (list(string_types) + [type(None)], None),
          'password': (list(string_types) + [type(None)], None),
          'email': (list(string_types) + [type(None)], None),

          'confirm': ([bool], True),

          'state': (list(string_types), 'present'),
        })

        return tmp


##    def _create_new_usrtoken(self, usr, cfg, 
##       result=None, rescol=None, state=None, **kwargs
##    ):
##        cfg.update(kwargs)
##
##        tmp = usr.impersonationtokens.create(cfg)
##
##        if rescol is not None:
##            rescol[tmp.name] = { 'token': tmp.token, 'state': state }
##
##        if result is not None:
##            result['changed'] = True
##
##        return tmp


    def _create_new_service_account(self, result):
        uname = self.get_taskparam('username')

        ##
        ## note: python-gitlab lib seems not to support service accounts
        ##   directly in high-level manor, but we still can get access
        ##   to "raw" gitlab api by using its low-level-api feature:
        ##
        ##     https://python-gitlab.readthedocs.io/en/stable/api-levels.html#lower-level-apis
        ##
        display.vv(
          "ActionModule[_create_new_service_account] :: calling"\
          " server rest api to create a new service account"
        )

        tmp = self.gitlab_client.http_post('/service_accounts')

        display.vvv(
          "ActionModule[_create_new_service_account] :: answer from server"\
          " for service account creation rest api request: {}".format(tmp)
        )

        ##
        ## finally change randomly generated service-account username
        ## to parameter requested "real" username, after this step we
        ## can handle/manage a service-account user basically like
        ## any other "normal" user
        ##
        botusr = self.gitlab_client.users.get(tmp['id'])
        botusr.username = uname
        botusr.save()


    def run_specific(self, result):
        ##
        ## do extended pre stuff
        ##

        utype = self.get_taskparam('user_type')

        prime_mail = self.get_taskparam('email')
        prime_confirm = self.get_taskparam('confirm')

        if utype == 'service_account':
            ## service-accounts dont do email confirmation, this is
            ## consistent with how gitlab handles its
            ## auto-generated default mail address
            prime_confirm = False

        mails = self.get_taskparam('extra_emails')
        mails[prime_mail] = prime_confirm

        ## normalize mails config
        for x in mails:
            v = mails[x]

            if not isinstance(v, collections.abc.Mapping):
                ## assume simple bool for confirm, norm to mapping
                v = {'confirm': v}

            mails[x] = v

        pre_stuff_changed = False

        ##
        ## user type is service-account, do service account special stuff
        ##
        if utype == 'service_account':

            display.vv(
              "ActionModule[run_specific] :: usertype is 'service_accounts',"\
              " do service accounts specific preparation steps ...".format(utype)
            )

            ## check if user exists already
            usr = self.get_glusr(non_exist_okay=True)
            create_usr = True

            if usr:
                create_usr = False
                pass
                ## check if existing user type matches expected service-account

                ##
                ## TODO: support automagically user type change by deleting and
                ##   recreating the user, be aware that fully deleting a user
                ##   might have major ramifications on gitserver depending on
                ##   how people have used it and what is connected to it like
                ##   ownership of repos and commits and so on
                ##

                ## wrong user type, only currently supported way
                ## to handle this is error out

            if create_usr:
                ##
                ## current case: non-existing service-account user, this must
                ##   currently be done by special extra api calls and is not
                ##   supported by upstream ansible gitlab user module, so we
                ##   use our own code here, also note that the api call used
                ##   here basically does not allow any user configuration (not
                ##   even choosing custom names), but after user is created we
                ##   can use standard user api calls (and so by extension also
                ##   standard ansible modules) to modify service-account user
                ##   like any other "normal" user
                ##
                self._create_new_service_account(result)
                pre_stuff_changed = True

        usr = self.get_glusr(non_exist_okay=True)

        if usr:
            display.vv(
              "ActionModule[run_specific] :: user exists already"\
              " check mail addresses ..."
            )

            mail_exists = False

            for em in usr.emails.list():
                ## if user exists already, ensure it has already an
                ## email "object" defined for its primary mail
                ## address, otherwise it cannot be updated / set
                ## by later usermod
                display.vvv(
                  "ActionModule[run_specific] :: found existing"\
                  " user mail address: {}".format(em)
                )

                if em.email == prime_mail:
                    display.vv(
                      "ActionModule[run_specific] :: currently selected"\
                      " primary mail address exists already, nothing to do"
                    )

                    mail_exists = True
                    break

            if not mail_exists:
                display.vv(
                  "ActionModule[run_specific] :: currently selected"\
                  " primary mail address is new, create it ..."
                )

                ##
                ## note: it is okay to create a new user with an
                ##   unconfirmed primary mail address, but changing
                ##   mail address for an already existing user to an
                ##   unconfirmed email address does not work, or at
                ##   least not if currently primary mail address is
                ##   confirmed already
                ##
                usr.emails.create({'email': prime_mail,
                  'skip_confirmation': not mails[prime_mail]['confirm']}
                )

        ##
        ## call usptream user management module
        ##

        modargs = {
          'confirm': prime_confirm,
        }

        for x in self.UPSTREAM_FORWARDING_PARAMS:
            if x in modargs:
                ## skip internally modified standard args
                continue

            xval = self.get_taskparam(x)

            if xval is not None:
                modargs[x] = xval

        cmdret = self.exec_gitlab_module(
           self.UPSTREAM_USER_MODULE,
           modargs=modargs,
        )

        ##
        ## do extended post stuff
        ##
        result.update(cmdret)

        if pre_stuff_changed:
            ## even if upstream call did not change anything, if pre-stuff
            ## already introduced changes force result changed to true
            result['changed'] = True

        usr = self.gitlab_user

        ## handle mail addresses, ensure all given addresses are
        ## defined and all unconfigured are removed
        handled_mails = []

        for em in usr.emails.list():
            if em.email in mails:
                ## mail exists already, nothing todo
                handled_mails.append(em.email)
                continue

            ## mail is "old" and not requested by
            ## current configuration anymore, delete it
            display.vv(
              "ActionModule[run_specific] :: delete"\
              " old not currently configured email"\
              " address '{}' ...".format(em)
            )

            usr.emails.delete(em.id)
            result['changed'] = True

        for k,v in mails.items():
            if k in handled_mails:
                continue ## noop

            ## create new and not yet existing extra mail address
            display.vv(
              "ActionModule[run_specific] :: create new"\
              " and not yet existing extra extra mail"\
              " address '{}' ...".format(k)
            )

            usr.emails.create({'email': k,
              'skip_confirmation': not v['confirm']}
            )

            result['changed'] = True

        return result


        state = self.get_taskparam('state')
        strict = self.get_taskparam('strict')

        usrtoks = self.get_taskparam('user_tokens')

        token_col = {}

        temp_token = None
        new_authtoken = None

        ## note: important to query the list before we might create 
        ##   a temp token as we obviously dont want to handle that
        tklist = usr.impersonationtokens.list(state='active')

        ##
        ## note: if the user where we mess with the tokens is the 
        ##   same user we authed with, there is a good chance that 
        ##   we update the same token we used as auth, so removing 
        ##   the same access way we currently use for managing, to 
        ##   avoid this we create a temporary acces token to make 
        ##   sure we have access to the server all the time
        ##
        if self.gitlab_client.user.username == usr.username:

            temp_token = {'name': str(uuid.uuid4()), 'scopes': ['api']}

            display.vv(
              "ActionModule[run_specific] :: user to handle"\
              " is auth user, create temp token: {}".format(temp_token)
            )

            ## note: to avoid name clashes with existing tokens use uuid here
            temp_token = self._create_new_usrtoken(usr, temp_token)

            display.vv(
              "ActionModule[run_specific] :: re-auth with temp token"
            )

            self.re_auth(private_token=temp_token.token)

            ## note: unfortunately this simple straigtforward way 
            ##   of re-authing with a new token does not work
            ##self.gitlab_client.private_token = temp_token.token
            ##self.gitlab_client.auth()

            ## note: all these objects still reference the old token 
            ##   after re-authing, we need to reset them to the new 
            ##   token explicitly
            usr = self.gitlab_user

            temp_token = next(filter(
               lambda x: x.name == temp_token.name, 
               usr.impersonationtokens.list(state='active')
            ))

        display.vv(
           "ActionModule[run_specific] :: handle existing"\
           " tokens: {}".format(list(map(lambda x: x.name, tklist)))
        )

        # handle existing tokens
        for ut in tklist:
            utname = ut.name

            display.vv(
              "ActionModule[run_specific] :: handle existing"\
              " token '{}'".format(utname)
            )

            newcfg = usrtoks.pop(utname, None)

            if not newcfg:
                # user token existing in gitlab not mentioned by 
                # ansible config, when in strict mode delete it, 
                # otherwise do nothing
                if strict:
                    token_col[utname] = { 'state': 'strict_removed' }
                    result['changed'] = True
                    ut.delete()
                continue

            display.vv(
              "ActionModule[run_specific] :: found ansible config"\
              " for current token: {}".format(newcfg)
            )

            # handle existing user token in gitlab also mentioned 
            # by ansible config depending on set state
            if state == 'update':
                display.vv(
                  "ActionModule[run_specific] :: do a token update"
                )

                # update is not really a thing in gitlab api, so we 
                # actually do a delete and recreate combo
                display.vvv(
                  "ActionModule[run_specific] :: update token delete"
                )

                ut.delete()

                display.vvv(
                  "ActionModule[run_specific] :: update token recreate"
                )

                self._create_new_usrtoken(
                   usr, newcfg, result, token_col, name=utname, 
                   state='updated'
                )

                display.vvv(
                  "ActionModule[run_specific] :: update token done"
                )

                continue

            if state == 'absent':
                display.vv(
                  "ActionModule[run_specific] :: do a token delete"
                )

                token_col[utname] = { 'state': 'absent_removed' }
                ut.delete()
                result['changed'] = True
                continue

            # note: state == 'present' is a noop here
            display.vv(
              "ActionModule[run_specific] :: existing token unchanged" 
            )

        display.vv(
           "ActionModule[run_specific] :: handle new"\
           " tokens: {}".format(list(usrtoks.keys()))
        )

        # handle new tokens
        for name, cfg in usrtoks.items():
            self._create_new_usrtoken(usr, cfg, result, token_col, 
              name=name, state='created'
            )

        ##usr.save()

        if temp_token:
            display.vv(
              "ActionModule[run_specific] :: remove temp token again"
            )

            temp_token.delete()

        result['user_tokens'] = token_col
        return result

