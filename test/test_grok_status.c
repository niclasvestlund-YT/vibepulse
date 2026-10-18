#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../components/app_tokens/grok_status_parse.h"
static void parse(const char *json, bool valid) {
  tk_grok_status s;
  memset(&s,0x5a,sizeof s);
  tk_grok_status before=s;
  assert(tk_grok_status_parse(json,strlen(json),&s)==valid);
  if (!valid) assert(!memcmp(&s,&before,sizeof s));
}
#define BASE "\"v\":1,\"enabled\":true,\"source\":\"none\",\"stale\":false,\"active\":0,\"doneToday\":0,\"bots\":[]"
static void numeric_rejections(void) {
  const char *keys[]={"ageSeconds","spentCents","limitCents","prepaidCents","usedPct","resetSeconds","windowSeconds"};
  const char *values[]={"-1","0.5","2147483648","true","null","\"1\"","[]","{}"};
  for (unsigned k=0;k<7;k++) for (unsigned v=0;v<8;v++) {
    char json[768];
    snprintf(json,sizeof json,"{" BASE ",\"%s\":%s}",keys[k],values[v]);
    parse(json,false);
  }
}
static void schema_rejections(void) {
  const char *root="{" BASE "}";
  const char *changes[][2]={
    {"\"v\":1,",""}, {"\"v\":1","\"v\":true"},
    {"\"enabled\":true,",""}, {"\"enabled\":true","\"enabled\":null"},
    {"\"source\":\"none\",",""}, {"\"source\":\"none\"","\"source\":\"other\""},
    {"\"source\":\"none\"","\"source\":1"},
    {"\"stale\":false,",""}, {"\"stale\":false","\"stale\":0"},
    {"\"active\":0,",""}, {"\"active\":0","\"active\":-1"},
    {"\"active\":0","\"active\":0.5"},
    {"\"doneToday\":0,",""}, {"\"doneToday\":0","\"doneToday\":true"},
    {",\"bots\":[]",""}, {"\"bots\":[]","\"bots\":null"},
    {"\"bots\":[]","\"bots\":[{\"name\":\"A\",\"name\":\"B\",\"state\":\"idle\"}]"}
  };
  for (unsigned i=0;i<sizeof changes/sizeof changes[0];i++) {
    const char *at=strstr(root,changes[i][0]);
    assert(at);
    char json[768];
    snprintf(json,sizeof json,"%.*s%s%s",(int)(at-root),root,changes[i][1],at+strlen(changes[i][0]));
    parse(json,false);
  }
  const char raw_nul[]="{" BASE "}\0";
  tk_grok_status s;
  assert(!tk_grok_status_parse(raw_nul,sizeof raw_nul-1,&s));
  parse("  {" BASE "} \n\t",true);
}
int main(void) {
  numeric_rejections();
  schema_rejections();
  const char *windows[]={"0","2678401","2147483647","1.5","1e100"};
  for (unsigned i=0;i<sizeof windows/sizeof windows[0];i++) {
    char json[768];
    snprintf(json,sizeof json,"{" BASE ",\"windowSeconds\":%s}",windows[i]);
    parse(json,false);
  }
  const int valid_windows[]={1,86400,604800,28*86400,30*86400,2678400};
  for (unsigned i=0;i<sizeof valid_windows/sizeof valid_windows[0];i++) {
    char json[768]; tk_grok_status status;
    snprintf(json,sizeof json,"{" BASE ",\"windowSeconds\":%d}",valid_windows[i]);
    assert(tk_grok_status_parse(json,strlen(json),&status));
    assert(status.has_window && status.window_seconds==valid_windows[i]);
  }
  parse("{" BASE ",\"windowSeconds\":1,\"windowSeconds\":1}",false);
  tk_grok_status known;
  const char *empty="{" BASE "}";
  assert(tk_grok_status_parse(empty,strlen(empty),&known));
  assert(known.enabled && !known.has_age && !known.has_spent && !known.has_limit &&
         !known.has_prepaid && !known.has_used && !known.has_reset && !known.has_window && !known.bot_count);
  const char *full="{" BASE ",\"ageSeconds\":3,\"spentCents\":1234,\"limitCents\":20000,\"prepaidCents\":4500,\"usedPct\":42,\"resetSeconds\":187200}";
  assert(tk_grok_status_parse(full,strlen(full),&known));
  assert(known.has_age && known.age_seconds==3 && known.has_spent && known.spent_cents==1234);
  assert(known.has_limit && known.limit_cents==20000 && known.has_prepaid && known.prepaid_cents==4500);
  assert(known.has_used && known.used_pct==42 && known.has_reset && known.reset_seconds==187200);
  const char *states[]={"working","waiting","done","idle","error"};
  for (unsigned i=0;i<5;i++) {
    char json[768];
    snprintf(json,sizeof json,"{\"v\":1,\"enabled\":true,\"source\":\"bots\",\"stale\":false,\"active\":1,\"doneToday\":2,\"bots\":[{\"name\":\"1234567890123456\",\"state\":\"%s\",\"activity\":\"1234567890123456\"}]}",states[i]);
    assert(tk_grok_status_parse(json,strlen(json),&known));
    assert(known.bot_count==1 && !strcmp(known.bots[0].state,states[i]));
  }
  parse("{\"v\":1,\"enabled\":false}",true);
  parse("{" BASE "}",true);
  const char *sources[]={"xai","openrouter","manual","bots","none"};
  for (unsigned i=0;i<5;i++) {
    char json[768];
    snprintf(json,sizeof json,"{\"v\":1,\"enabled\":true,\"source\":\"%s\",\"stale\":true,\"active\":2,\"doneToday\":3,\"bots\":[{\"name\":\"A\",\"state\":\"waiting\",\"activity\":\"\"}],\"usedPct\":100,\"spentCents\":2147483647,\"limitCents\":0,\"prepaidCents\":1,\"resetSeconds\":0,\"ageSeconds\":5,\"label\":\"\"}",sources[i]);
    parse(json,true);
  }
  const char *extras[]={"\"unknown\":0","\"v\":1","\"ageSeconds\":-1","\"ageSeconds\":1.5","\"ageSeconds\":2147483648","\"ageSeconds\":true","\"spentCents\":null","\"limitCents\":\"1\"","\"prepaidCents\":-1","\"usedPct\":101","\"resetSeconds\":-1","\"label\":\"12345678901234567\"","\"label\":\"\\u0000\"","\"label\":\"\\n\"","\"label\":\"é\"","\"label\":2"};
  for (unsigned i=0;i<sizeof extras/sizeof extras[0];i++) {
    char json[768]; snprintf(json,sizeof json,"{" BASE ",%s}",extras[i]); parse(json,false);
  }
  const char *bad[]={"{}","[]","{\"v\":2,\"enabled\":false}","{\"v\":1,\"enabled\":0}","{\"v\":1,\"enabled\":false,\"stale\":true}","{" BASE "}x","{" BASE "}{}"};
  for (unsigned i=0;i<sizeof bad/sizeof bad[0];i++) parse(bad[i],false);
  const char *bots[]={"{}","[{\"name\":\"\",\"state\":\"idle\"}]","[{\"name\":\"12345678901234567\",\"state\":\"idle\"}]","[{\"name\":\"A\",\"state\":\"other\"}]","[{\"name\":\"A\",\"state\":\"idle\",\"x\":1}]","[{\"name\":\"A\",\"state\":\"idle\",\"activity\":\"12345678901234567\"}]","[1]","[{}, {}, {}, {}, {}]"};
  for (unsigned i=0;i<sizeof bots/sizeof bots[0];i++) {
    char json[768]; snprintf(json,sizeof json,"{\"v\":1,\"enabled\":true,\"source\":\"bots\",\"stale\":false,\"active\":0,\"doneToday\":0,\"bots\":%s}",bots[i]);parse(json,false);
  }
  tk_grok_status s;
  assert(!tk_grok_status_parse(NULL,0,&s));
  assert(!tk_grok_status_parse("{}",2,NULL));
  puts("OK: strict Grok payloads, bounds and atomic rejection");
}
