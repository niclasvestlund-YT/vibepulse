#include "../components/app_openpulse/model.h"
#include <assert.h>
#include <math.h>
#include <string.h>
int main(void) {
    op_snapshot result;
    const char *json="{\"schema\":1,\"currency\":\"USD\",\"state\":\"fresh\",\"accountState\":\"disabled\",\"keyIndex\":0,\"keyCount\":1,\"day\":0,\"month\":38.42,\"week\":null,\"limitState\":\"unlimited\",\"budgetLevel\":\"warning\",\"name\":\"Development\",\"demo\":true}";
    assert(op_parse(json,strlen(json),&result));
    assert(result.day==0 && isnan(result.week) && fabs(result.month-38.42)<0.001);
    assert(result.unlimited && result.demo && result.level==1);
    assert(isnan(result.balance) && isnan(result.age));
    assert(!strcmp(result.name,"Development"));
    assert(!op_parse("{}",2,&result));
    assert(!op_parse(json,5,&result));
    assert(!op_parse(json,4097,&result));
    return 0;
}
