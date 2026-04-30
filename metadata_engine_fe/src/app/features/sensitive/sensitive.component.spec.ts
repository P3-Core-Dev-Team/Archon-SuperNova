import { ComponentFixture, TestBed } from '@angular/core/testing';

import { SensitiveComponent } from './sensitive.component';

describe('SensitiveComponent', () => {
  let component: SensitiveComponent;
  let fixture: ComponentFixture<SensitiveComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SensitiveComponent]
    })
    .compileComponents();
    
    fixture = TestBed.createComponent(SensitiveComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
